from django.contrib import admin
from .models import (
    ROI,
    Omero,
    OmeroFile,
    Representation,
    Sample,
    Experiment,
    Thumbnail,
    Metric,
    UserMeta,
    Stage,
    Position,
)
from guardian.admin import GuardedModelAdmin


# Register your models here.
class SingleTextInputFilter(admin.ListFilter):
    """
    renders filter form with text input and submit button
    """

    parameter_name = None
    template = "admin/textinput_filter.html"

    def __init__(self, request, params, model, model_admin):
        super(SingleTextInputFilter, self).__init__(request, params, model, model_admin)

        if self.parameter_name is None:
            raise admin.ImproperlyConfigured(
                "The list filter '%s' does not specify "
                "a 'parameter_name'." % self.__class__.__name__
            )

        if self.parameter_name in params:
            value = params.pop(self.parameter_name)
            self.used_parameters[self.parameter_name] = value

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(**{self.parameter_name: self.value()})

    def value(self):
        """
        Returns the value (in string format) provided in the request's
        query string for this filter, if any. If the value wasn't provided then
        returns None.
        """
        return self.used_parameters.get(self.parameter_name, None)

    def has_output(self):
        return True

    def expected_parameters(self):
        """
        Returns the list of parameter names that are expected from the
        request's query string and that will be used by this filter.
        """
        return [self.parameter_name]

    def choices(self, cl):
        all_choice = {
            "selected": self.value() is None,
            "query_string": cl.get_query_string({}, [self.parameter_name]),
            "display": "All",
        }
        return (
            {
                "get_query": cl.params,
                "current_value": self.value(),
                "all_choice": all_choice,
                "parameter_name": self.parameter_name,
            },
        )


class SampleNameListFilter(SingleTextInputFilter):
    title = "Sample Name"
    parameter_name = "sample__name__icontains"


class ExperimentNameFilter(SingleTextInputFilter):
    title = "Experiment Name"
    parameter_name = "sample__experiments__name__icontains"


class SampleAdmin(admin.ModelAdmin):
    list_filter = ("experiments",)
    search_fields = ("name",)


class RepresentationAdmin(GuardedModelAdmin):
    list_filter = (SampleNameListFilter, ExperimentNameFilter)

    def lookup_allowed(self, key, value):
        return True


admin.site.register(UserMeta)
admin.site.register(Representation, RepresentationAdmin)
admin.site.register(Sample, SampleAdmin)
admin.site.register(Experiment)
admin.site.register(OmeroFile)
admin.site.register(Omero)
admin.site.register(Thumbnail)
admin.site.register(Metric)
admin.site.register(Stage)
admin.site.register(Position)
