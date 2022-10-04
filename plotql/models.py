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
    name = models.CharField(max_length=255, help_text="The name of the plot")
    description = models.TextField(help_text="A description of the plot")
    created_at = models.DateTimeField(auto_now_add=True, help_text="When was this plot created")
    updated_at = models.DateTimeField(auto_now=True, help_text="When was this plot last updated")
    query = models.CharField(max_length=10000, default=default_plot_query, help_text="The PlotQL query (see documentation for PlotQL)")
    creator = models.ForeignKey(
        get_user_model(),
        related_name="plots",
        on_delete=models.CASCADE,
        help_text="The user who created this plot",
    )
