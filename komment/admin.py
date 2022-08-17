from django.contrib import admin
from guardian.admin import GuardedModelAdmin
from komment.models import Comment

# Register your models here.
class CommentAdmin(GuardedModelAdmin):
    pass


admin.site.register(Comment, CommentAdmin)
