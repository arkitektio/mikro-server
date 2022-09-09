from django.db import models
from django.contrib.auth import get_user_model

# Create your models here.

default_plot_query = """
query {
    GROUP: experiment(id: 42) {
      OBJECT: id
      TYPE: __typename
      NAME: name
      
      GROUP_HYPER_AS_BAD_SAMPLES: samples(tags: "bad") {
        OBJECT: id
         TYPE: __typename
        NAME: name
        GROUPS: representations(variety: VOXEL){
          NAME: name
          OBJECT: id
          TYPE: __typename


          FLATDATUM_OBJECT_AS_INDEX: id
          FLATDATUM_VALUE_AS_INDEX: id
          FLATDATUM_TYPE_AS_INDEX: __typename
  
          DATUM_AS_TIME: omero {
            VALUE_FROM_DATE: acquisitionDate
          }
          
          DATUM_AS_EXPOSURE_TIME: omero{
            OBJECT: id
            TYPE: __typename
            VALUE_FROM_SUM: planes {
              VALUE_FROM_FLOAT: exposureTime
            }
          }
          
          DATUM_FIRST_AS_INCREASING: metrics(keys: "Increasing"){
            VALUE_FROM_INT: value
          }
          
          DATUM_FIRST_AS_CELL_AREA: labels {
            OBJECT: id
            TYPE: __typename
            VALUE_FROM_SUM: features(keys: "Area"){
              VALUE_FROM_FLOAT: value
            }
          }
          
        }
          
        }
        
      

      GROUP_HYPER_AS_ELEMENTAL_SAMPLES: samples(tags: "elemental") {
        OBJECT: id
         TYPE: __typename
        NAME: name
        GROUPS: representations(variety: VOXEL){
          NAME: name
          OBJECT: id
          TYPE: __typename


          FLATDATUM_OBJECT_AS_INDEX: id
          FLATDATUM_VALUE_AS_INDEX: id
          FLATDATUM_TYPE_AS_INDEX: __typename
  
      
          DATUM_AS_EXPOSURE_TIME: omero{
            OBJECT: id
            TYPE: __typename
            VALUE_FROM_SUM: planes {
              VALUE_FROM_FLOAT: exposureTime
            }
          }
          
          
        }
          
        }
  }
}
"""


class Plot(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    query = models.CharField(max_length=10000, default=default_plot_query)
    creator = models.ForeignKey(
        get_user_model(),
        related_name="plots",
        on_delete=models.CASCADE,
    )
