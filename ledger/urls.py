from django.urls import path

from . import views

urlpatterns = [
    path("health", views.health),
    path("api/callbacks/<str:token>", views.callback),
    path("api/flows/<str:ref>/wait", views.open_wait),
    path("api/applications", views.applications),
    path("api/applications/<str:ref>", views.application_detail),
    path("api/parked", views.parked),
    path("api/parked/<int:event_id>/decision", views.decide),
    path("api/rejected", views.rejected),
    path("api/demo/applications", views.demo_new_application),
    path("api/demo/partner/<str:ref>", views.demo_partner),
    path("api/demo/reset", views.demo_reset),
]
