from django.urls import path

from files.consumers import AskVaultConsumer


websocket_urlpatterns = [
    path("ws/ask-vault/", AskVaultConsumer.as_asgi()),
]
