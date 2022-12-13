from django.contrib.auth import get_user_model

def fill_created(info, imitator = None ):
    creator = info.context.user or (
            get_user_model().objects.get(sub=imitator) if imitator else None
        )
    return {"created_by": creator, "created_through": info.context.client}