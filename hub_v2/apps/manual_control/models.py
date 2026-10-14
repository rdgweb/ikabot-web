"""Manual control of a game account (N-72).

The operator takes one game account over for a while: the automation of that
account stops, the game opens in the operator's browser through the agent's own
session and proxy, and the automation comes back by itself when the tab closes.

The control state of an account is whether it has an open session here:

    no open session       AUTOMATION_ACTIVE
    status = pausing      PAUSING         (waiting for the running job, opening the session in the agent)
    status = active       MANUAL_CONTROL
    status = resuming     RESUMING        (handing the held jobs back)
"""

from django.conf import settings
from django.db import models

from apps.orgs.scope import OrgScopedManager

from core.mixins.models import UUIDTimestampModel


class ManualControlSession(UUIDTimestampModel):
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    PAUSING = "pausing"
    ACTIVE = "active"
    RESUMING = "resuming"
    ENDED = "ended"
    STATUS_CHOICES = [
        (PAUSING, "Preparando"),
        (ACTIVE, "Controle manual"),
        (RESUMING, "Retomando"),
        (ENDED, "Encerrada"),
    ]
    OPEN_STATUSES = (PAUSING, ACTIVE, RESUMING)

    game_account = models.ForeignKey(
        "accounts.GameAccount", on_delete=models.CASCADE, related_name="manual_control_sessions",
    )
    node = models.ForeignKey("accounts.Node", on_delete=models.CASCADE, related_name="manual_control_sessions")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="manual_control_sessions",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PAUSING, db_index=True)
    # The game account id while the session is open, NULL afterwards: the unique
    # index is what makes a second open session for the same account impossible
    # (a conditional constraint would not exist on MariaDB).
    open_key = models.CharField(max_length=40, unique=True, null=True, blank=True, default=None)
    # Public name of the session (the subdomain label). Unpredictable, never the account id.
    token = models.CharField(max_length=40, unique=True)
    # Only hashes are stored: the one-time key that lets the browser in, and the cookie it gets.
    start_key_hash = models.CharField(max_length=64, blank=True, default="")
    cookie_hash = models.CharField(max_length=64, blank=True, default="")
    # Pending "open" request to the agent.
    open_request_id = models.CharField(max_length=40, blank=True, default="")
    open_requested_at = models.DateTimeField(null=True, blank=True)
    server_host = models.CharField(max_length=120, blank=True, default="")
    activated_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    # last request for the game itself (a tab left open and forgotten stops counting)
    last_request_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=32, blank=True, default="")
    error = models.TextField(blank=True, default="")
    request_count = models.PositiveIntegerField(default=0)
    # N-99: which proxy the agent said this session leaves through. A label of the hub's
    # own proxy list ("P218"), never an address.
    proxy_label = models.CharField(max_length=24, blank=True, default="")
    # account (reserved for the account) | node | other | unknown | none | "" (agent did not say)
    proxy_scope = models.CharField(max_length=16, blank=True, default="")
    # ok (exit address is the proxy's) | differs | direct (the agent's own address!) | unchecked | ""
    exit_check = models.CharField(max_length=16, blank=True, default="")
    # the address the outside world saw the session leave from, as the agent measured it
    exit_ip = models.CharField(max_length=45, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["game_account", "status"], name="manual_ctl_ga_status_idx"),
        ]

    def __str__(self):
        return f"ManualControl {self.id} [{self.status}] ga={self.game_account_id}"

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES


class HeldJob(models.Model):
    """A job that tried to start while its account was not in automation.

    It stays exactly as it was (queued or scheduled); this row is how the release
    knows which jobs to hand back to the agent.
    """
    objects = OrgScopedManager()  # N-106: only the organization of whoever is logged in

    session = models.ForeignKey(ManualControlSession, on_delete=models.CASCADE, related_name="held_jobs")
    job = models.OneToOneField("jobs.Job", on_delete=models.CASCADE, related_name="manual_hold")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"HeldJob job={self.job_id} session={self.session_id}"
