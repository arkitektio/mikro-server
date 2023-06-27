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

Use the provided docker-compose.yaml to run the the service.

```bash
docker compose up
```

This docker-compose.yaml will start the following services:
- mikro
- postgres
- minio


## Roadmap

- [ ] Add django-audit-log (by extending it with client_id, assignation_id, user_id) to map changes to models