from django.contrib import admin

from .models import Application, CallbackEvent, RejectedCallback, TimelineEntry, WaitStep

admin.site.register(Application)
admin.site.register(WaitStep)
admin.site.register(CallbackEvent)
admin.site.register(RejectedCallback)
admin.site.register(TimelineEntry)
