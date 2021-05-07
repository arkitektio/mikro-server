"""elements URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.1/topics/http/urls/
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
from django.urls import path
from django.shortcuts import render
from graphene_django.views import GraphQLView
from django.views.decorators.csrf import csrf_exempt
from django.conf.urls import include, url
from balder.autodiscover import autodiscover
from balder.views import BalderView
import elements.arkitekt

# Autodiscover for all of the Balder Modules in the installed Apps



def index(request):
        # Render that in the index template
    return render(request, "index-oslo.html")


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', index, name='index'),
    url(r'^graphql$', BalderView),
     url(r'^ht/', include('health_check.urls')),

]
