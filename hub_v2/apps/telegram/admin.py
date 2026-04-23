from django.contrib import admin

from .models import (
    MessageAudit,
    NotificationTemplate,
    TelegramAccountConfig,
    TelegramBotConfig,
    TelegramIncomingCommand,
)


admin.site.register(TelegramBotConfig)
admin.site.register(TelegramAccountConfig)
admin.site.register(NotificationTemplate)
admin.site.register(TelegramIncomingCommand)
admin.site.register(MessageAudit)
