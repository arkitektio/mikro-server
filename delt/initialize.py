import django.db.models.options as options







def initialize():
    # TOp level change of default parameters
    options.DEFAULT_NAMES = options.DEFAULT_NAMES + ('arkitekt','extenders')
