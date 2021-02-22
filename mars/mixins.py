from django.forms.models import model_to_dict
import pandas as pd


class JupyterBeautifier:

    def _repr_html_(self):

        return pd.DataFrame.from_records([model_to_dict(self)])._repr_html_()
