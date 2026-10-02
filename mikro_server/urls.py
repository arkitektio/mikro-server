"""
URL configuration for mikro_server project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from kante.path import dynamicpath
from health_check.views import MainView
from django.views.decorators.csrf import csrf_exempt
from rekuest_service.views import answers_challenge
from mikro_server.service import service as rekuest_service

urlpatterns = [
    dynamicpath("admin/", admin.site.urls),
    dynamicpath("ht",  answers_challenge(csrf_exempt(MainView.as_view())), name="health_check"),
    # The hub's rekuest runs this service's periodic work through here (internal network only).
    *rekuest_service.urls,
]
