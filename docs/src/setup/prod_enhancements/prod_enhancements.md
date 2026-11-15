# Further Production Enhancements { #geonode-production-enhancements }

## GeoServer Production Settings

### JVM Settings: Memory And GeoServer Options

The `.env` file provides a way to customize GeoServer JVM options.

The `GEOSERVER_JAVA_OPTS` variable allows you to tune up the GeoServer container and enable specific GeoServer options.

```bash
GEOSERVER_JAVA_OPTS=
    -Djava.awt.headless=true -Xms4G -Xmx4G -XX:PerfDataSamplingInterval=500
    -XX:SoftRefLRUPolicyMSPerMB=36000 -XX:-UseGCOverheadLimit -XX:+UseConcMarkSweepGC
    -XX:+UseParNewGC -XX:ParallelGCThreads=4 -Dfile.encoding=UTF8 -Djavax.servlet.request.encoding=UTF-8
    -Djavax.servlet.response.encoding=UTF-8 -Duser.timezone=GMT
    -Dorg.geotools.shapefile.datetime=false -DGS-SHAPEFILE-CHARSET=UTF-8 -DGEOSERVER_CSRF_DISABLED=true -DPRINT_BASE_URL=http://geoserver:8080/geoserver/pdf
```

`-Djava.awt.headless (true)`

Work with graphics-based applications in Java without an actual display, keyboard, or mouse.
A typical use case of UI components running in a headless environment could be an image converter app. Though it needs graphics data for image processing, a display is not really necessary. The app could be run on a server and converted files saved or sent over the network to another machine for display.

`-Xms4G -Xmx4G`

This means that your JVM will be started with Xms amount of memory and will be able to use a maximum of Xmx amount of memory. Above will start a JVM with 2 GB of memory and will allow the process to use up to 4 GB of memory. You need to adjust this value depending on your available RAM.

`-DGEOSERVER_CSRF_DISABLED (True)`

The GeoServer web admin employs a CSRF (Cross-Site Request Forgery) protection filter that will block any form submissions that did not appear to originate from GeoServer. This can sometimes cause problems for certain proxy configurations. You can disable the CSRF filter by setting the `GEOSERVER_CSRF_DISABLED` property to `true`.
[Further details](https://docs.geoserver.org/main/en/user/security/webadmin/csrf/)

Whenever you need to change one or more of the JVM options, you will need to restart the GeoServer Docker container.

```bash
# Hard restart of the container: the only way to update the .env variables
docker-compose up -d geoserver
```

This command will **preserve** all the GeoServer configuration and data, since the `GEOSERVER_DATA_DIR` is stored on a Docker static volume.

Nevertheless, any change you have made manually to the container, for example adding a new plugin to GeoServer or updating some JARs into the `WEB-INF/lib` library folder, will be lost.

You will need to add the JARs again and restart GeoServer *softly*.

```bash
# Soft restart of the container: the .env variables won't be updated
docker-compose restart geoserver
```

### Global And Services Settings

- Check the GeoServer memory usage and status. Ensure the `GEOSERVER_DATA_DIR` path points to the static volume.

  ![](img/production_geoserver_001.png){ align=center width="350px" }
  /// caption
  *GeoServer Status*
  ///

- GeoServer `Global Settings`: make sure the `Proxy Base Url` points to the public URL and the `LOGGING` levels are set to `Production Mode`.

  ![](img/production_geoserver_002.png){ align=center width="350px" }
  /// caption
  *Global Settings*
  ///

- GeoServer `Image Processing Settings`: unless you are using some specific renderer or GeoServer plugin, use the following recommended options.

    !!! Note
        Further details are available in the [GeoServer image processing documentation](https://docs.geoserver.org/main/en/user/configuration/image_processing/index.html#image-processing).

  ![](img/production_geoserver_003.png){ align=center width="350px" }
  /// caption
  *Image Processing Settings*
  ///

- Tune up `GeoServer Services Configuration`: `WCS`, `WFS`, `WMS` and `WPS`.

  - **WCS**: Update the limits according to your needs. Do not use very high values, as this will make GeoServer prone to DoS attacks.

    ![](img/production_geoserver_004.png){ align=center width="350px" }
    /// caption
    *WCS Resource Consuption Limits*
    ///

  - **WMS**: Specify here the SRS list you are going to use. Empty means all the ones supported by GeoServer, but be careful since the `GetCapabilities` output will become huge.

    ![](img/production_geoserver_005.png){ align=center width="350px" }
    /// caption
    *WMS Supported SRS List*
    ///

  - **WMS**: `Raster Rendering Options` allows you to tune up the WMS output for better performance or quality. Best Performance: `Nearest Neighbour` - Best Quality: `Bicubic`.

    !!! warning
        Raster images should always be optimized before being ingested into GeoNode. The general recommendation is to **never** upload a non-processed GeoTIFF image to GeoNode.

        Further details:

        - [Enterprise raster training](https://geoserver.geo-solutions.it/edu/en/enterprise/raster.html)
        - [Advanced GDAL raster data](https://geoserver.geo-solutions.it/edu/en/raster_data/advanced_gdal/index.html)

    ![](img/production_geoserver_006.png){ align=center width="350px" }
    /// caption
    *WMS Raster Rendering Options*
    ///

  - **WMS**: Update the limits according to your needs. Do not use very high values, as this will make GeoServer prone to DoS attacks.

    ![](img/production_geoserver_007.png){ align=center width="350px" }
    /// caption
    *WMS Resource Consuption Limits*
    ///

### GeoWebCache DiskQuota On Postgis

By default, GeoWebCache DiskQuota is disabled. That means the layers cache might potentially grow indefinitely.

GeoWebCache DiskQuota should always be enabled on a production system. If it is enabled, this **must** be configured to use a DB engine like PostGIS to store its indexes.

- First of all, ensure `Tile Caching` is enabled on all available layers.

    !!! Note
        GeoNode typically does this automatically for you. It is worth double-checking anyway.

  ![](img/production_geoserver_008.png){ align=center width="350px" }
  /// caption
  *Tile Caching: Tiled Datasets*
  ///

- Configure `Disk Quota` by providing the connection string to the DB Docker container as specified in the `.env` file.

  ![](img/production_geoserver_009.png){ align=center width="350px" }
  /// caption
  *Tile Caching: Disk Quota Configuration*
  ///

### GeoServer Cloud ACL Rules

This branch synchronizes GeoNode resource permissions with the GeoServer Cloud ACL service. ACL owns and stores the authorization rules; it does not use GeoFence or GeoFence's PostGIS/H2 datastore. Configure and back up the ACL service and GeoServer Cloud catalog according to the [GeoNode + GeoServer Cloud guide](../docker/geoserver-cloud.md).

## Fixup GeoNode Datasets Permissions

To reconcile GeoNode dataset permissions with the GeoServer Cloud ACL service, run the ACL synchronization command from the Django container:

```bash
docker compose exec django python manage.py sync_security_rules
```

## Regenerate GeoNode Datasets Thumbnails

The following procedure allows you to *batch* regenerate all datasets thumbnails:

```bash
# Enter the GeoNode Docker Container
docker-compose exec django bash

# Run the `sync_geonode_datasets` management command
./manage.sh sync_geonode_datasets --updatethumbnails
```

## Regenerate GeoNode Datasets BBOXES

The following procedure allows you to *batch* regenerate all datasets BBOXES:

```bash
# Enter the GeoNode Docker Container
docker-compose exec django bash

# Run the `sync_geonode_datasets` management command
./manage.sh sync_geonode_datasets --updatebbox
```

## Fixup GeoNode Datasets Metadata And Download Links

The following procedure allows you to fix up broken or incorrect metadata links:

```bash
# Enter the GeoNode Docker Container
docker-compose exec django bash

# Run the `set_all_datasets_metadata` management command
./manage.sh set_all_datasets_metadata -d
```

It is also possible to *force* purging the links before regenerating:

```bash
# Enter the GeoNode Docker Container
docker-compose exec django bash

# Run the `set_all_datasets_metadata` management command
./manage.sh set_all_datasets_metadata -d --prune
```

## Migrate GeoNode To A New Hostname

In case you need to move your instance to another domain, for example from `https://my_geonode.geonode.org/` to `https://prod_geonode.geonode.org/`, follow the procedure below:

- Update the `.env` file by specifying the new name accordingly.
- Restart the GeoNode Docker container.

```bash
docker-compose up -d geonode
```

- Run the following management commands from inside the GeoNode Docker container.

```bash
# Enter the GeoNode Docker Container
docker-compose exec django bash

# Run the `migrate_baseurl` management command
./manage.sh migrate_baseurl --source-address=my_geonode.geonode.org --target-address=prod_geonode.geonode.org

# Run the `set_all_datasets_metadata` management command
./manage.sh set_all_datasets_metadata -d
```

## Add Huge Or DB Datasets To Your Instance

Uploading huge datasets, or DB tables, to GeoNode from the `Web Upload Interface` is not really possible sometimes.

The suggested procedure in such cases is the following:

- Add the dataset to `GeoServer` first directly.

  You must upload the data into the GeoServer Docker container static volume first and then add the layer manually through the `GeoServer Admin GUI`.

- Once the dataset is correctly configured on GeoServer, run the following management command from inside the GeoNode Docker container.

```bash
# Enter the GeoNode Docker Container
docker-compose exec django bash

# Run the `updatelayers` management command
./manage.sh updatelayers -w <workspace_name> -f <layer_name>
```
