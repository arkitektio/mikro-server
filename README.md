# mikro

Mikro is a Microscopy Data Management Mikroservice (thats a lot of M's).
It is a Django application that provides a GraphQL for managing microscopy data
and was originally developed for the Arkitekt platform.

It provides the following functionality:

- Manage microscopy data
    - Images (2D, 3D, 4D, 5D, Voxel, Segmentation,...)
    - Rois on these images
    - Labels on these images
    - Metrics associated with the Images
- Manage microscopy metadata (associated with the data through views)
    - Microscope
    - Objective
    - Channel
    - ...
- Manage microscopy data provenance
    - Users (who)
    - Time (when)
    - Clients (with what (which software))
    - Assignation (doing what, (which analysis task))

## Installation

Right now the installation is a bit cumbersome if not used with the Arkitekt platform.
This will be improved in the future.

For now please isntall either through [konstruktor](https://github.com/jhnnsrs/konstruktor) or
via the development build of the arkitket platform [arkitekt-server](https://github.com/jhnnsrs/arkitekt-server)


## Roadmap

- [ ] Add django-audit-log (by extending it with client_id, assignation_id, user_id) to map changes to models