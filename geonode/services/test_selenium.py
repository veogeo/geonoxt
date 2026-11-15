#########################################################################
#
# Copyright (C) 2021 OSGeo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################
import os
from urllib.parse import urlsplit

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.support.ui import WebDriverWait

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model


# from selenium.webdriver.chrome.webdriver import WebDriver
# from webdriver_manager.chrome import ChromeDriverManager
from . import enumerations, forms


@override_settings(ALLOWED_HOSTS=["*"])
class WmsServiceHarvestingTestCase(StaticLiveServerTestCase):
    # Bind to the container interface and let the OS choose a free port.
    # A remote Selenium node reaches this LiveServer through the Docker DNS
    # name "django"; local Firefox uses localhost.
    fixtures = ["initial_data.json", "group_test_data.json", "default_oauth_apps.json"]
    host = "0.0.0.0"
    port = 0
    selenium = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        try:
            cls.client = Client()
            UserModel = get_user_model()
            cls.user = UserModel.objects.create_user(
                username="test",
                password="test@123",
                first_name="ather",
                last_name="ashraf",
                is_staff=True,
                is_active=True,
                is_superuser=False,
            )
            cls.user.save()
            cls.client.login(username="test", password="test@123")
            cls.cookie = cls.client.cookies["sessionid"]
            remote_url = os.environ.get("NOXT_SELENIUM_REMOTE_URL")
            if not remote_url and getattr(settings, "NOXT_GEOSERVER_CLOUD_ENABLED", False):
                raise RuntimeError(
                    "Cloud-mode harvesting tests require NOXT_SELENIUM_REMOTE_URL; "
                    "set it in .env_test_cloud and run ./test-cloud.sh."
                )
            options = webdriver.FirefoxOptions()
            options.add_argument("-headless")
            if remote_url:
                cls.selenium = webdriver.Remote(command_executor=remote_url, options=options)
                browser_server_url = f"http://django:{cls.server_thread.port}"
            else:
                cls.selenium = webdriver.Firefox(options=options)
                browser_server_url = f"http://localhost:{cls.server_thread.port}"
            cls.selenium_server_url = browser_server_url
            cls.selenium.implicitly_wait(10)
            cls.selenium.get(f"{browser_server_url}/")
            cls.selenium.add_cookie({"name": "sessionid", "value": cls.cookie.value, "secure": False, "path": "/"})
            cls.selenium.refresh()
            reg_url = reverse("register_service")
            cls.client.get(reg_url)

            url = "https://gs-stable.geo-solutions.it/geoserver/wms"
            service_type = enumerations.WMS
            form_data = {"url": url, "type": service_type}
            forms.CreateServiceForm(form_data)

            response = cls.client.post(reverse("register_service"), data=form_data)
            target = urlsplit(response.url)
            cls.selenium.get(cls.selenium_server_url + target.path + (f"?{target.query}" if target.query else ""))
            cls.selenium.refresh()
        except Exception as e:
            cls.selenium_setup_error = str(e)
            print(cls.selenium_setup_error)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if cls.selenium:
            cls.selenium.quit()

    def test_harvest_resources(self):
        self.assertIsNotNone(
            self.selenium,
            "Firefox did not start; harvesting UI assertions were not executed. "
            f"Setup detail: {getattr(self, 'selenium_setup_error', 'no remote Selenium URL configured?')}",
        )
        if self.selenium:
            self.assert_resource_table_status(False)

            # Use the IDs and values rendered by the current harvesting view;
            # hard-coded Atlantis layer names belong to an older fixture.
            for filter_id, cell_index in (
                ("unique-identifier-filter", 1),
                ("title-filter", 2),
                ("desc-filter", 3),
                ("type-filter", 4),
            ):
                clear_button = self.selenium.find_element(By.ID, "btnClearFilter")
                self.selenium.execute_script("arguments[0].click();", clear_button)
                table = self.selenium.find_element(By.ID, "resource_table")
                source_rows = table.find_elements(By.CSS_SELECTOR, 'tbody tr:not([name="filter_row"])')
                filter_value = next(
                    (row.find_elements(By.TAG_NAME, "td")[cell_index].text.strip() for row in source_rows
                     if row.find_elements(By.TAG_NAME, "td")[cell_index].text.strip()),
                    None,
                )
                if filter_id == "desc-filter" and filter_value is None:
                    # The remote WMS fixture may omit abstracts for every
                    # layer on the current page, leaving nothing to filter.
                    continue
                self.assertIsNotNone(filter_value, f"No source value available for {filter_id}.")
                if filter_id == "desc-filter" and len(filter_value) > 20:
                    filter_value = f"{filter_value[:19]}…"
                self.selenium.find_element(By.ID, filter_id).send_keys(filter_value)
                filter_button = self.selenium.find_element(By.ID, f"btn-{filter_id}")
                self.selenium.execute_script("arguments[0].click();", filter_button)
                self.assert_resource_table_status(True)

            clear_button = self.selenium.find_element(By.ID, "btnClearFilter")
            self.selenium.execute_script("arguments[0].click();", clear_button)
            self.assert_resource_table_status(False)

    def assert_resource_table_status(self, is_row_filtered):
        def get_table_status(driver):
            try:
                table = driver.find_element(By.ID, "resource_table")
                rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
                result = {"filter_row_count": 0, "visible_rows_count": 0, "hidden_row_count": 0}
                for row in rows:
                    if row.get_attribute("name") == "filter_row":
                        result["filter_row_count"] += 1
                    if row.value_of_css_property("display") == "none":
                        result["hidden_row_count"] += 1
                    else:
                        result["visible_rows_count"] += 1
                if is_row_filtered and not result["filter_row_count"]:
                    return False
                if not is_row_filtered and result["visible_rows_count"] == 0:
                    return False
                return result
            except StaleElementReferenceException:
                # Filtering replaces the table rows asynchronously. Retry
                # against the new DOM instead of reading a detached element.
                return False

        result = WebDriverWait(self.selenium, 10).until(get_table_status)

        if is_row_filtered:
            self.assertTrue(result["filter_row_count"] > 0)
            self.assertEqual(result["visible_rows_count"], result["filter_row_count"])
            self.assertEqual(result["hidden_row_count"], self._source_row_count)
        else:
            self.assertEqual(result["filter_row_count"], 0)
            if not hasattr(self, "_source_row_count"):
                self._source_row_count = result["visible_rows_count"]
            self.assertGreater(self._source_row_count, 0, "The WMS service returned no harvestable resources.")
            self.assertEqual(result["visible_rows_count"], self._source_row_count)
            self.assertEqual(result["hidden_row_count"], 0)
