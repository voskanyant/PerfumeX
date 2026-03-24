"""
URL configuration for perfumex project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
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
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from prices import views as price_views

urlpatterns = [
    path("", price_views.ViewerProductListView.as_view(), name="viewer_home"),
    path("products/search/", price_views.ViewerProductSearchView.as_view(), name="viewer_product_search"),
    path("products/<int:pk>/", price_views.ViewerProductDetailView.as_view(), name="viewer_product_detail"),
    path("account/profile/", price_views.UserProfileUpdateView.as_view(), name="user_profile"),
    path("admin/", include("prices.urls")),
    path("django-admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
