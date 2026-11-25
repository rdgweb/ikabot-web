# Personal account (N-236, N-237, N-240)

`/conta/` contains Profile, Security and Preferences. It always authorizes against
the authenticated user, independently of organization, plan and platform role.
Personal data never grants privileges or moves game accounts. N-252 navigation
grouping, N-235 notification delivery and N-249 two-factor authentication are separate.

## Profile and preferences

The existing first/last name fields are preserved. Display name and preferences live
in UserProfile. Avatars accept static JPEG/PNG/WebP up to 2 MB and 16 million pixels;
Pillow verifies and re-encodes them as a clean 256-pixel JPEG (maximum 60 KB), dropping
source metadata. The binary is stored in the database, so no new media volume or public
upload endpoint is needed. `/conta/avatar/` serves only the requester's picture with
private/no-store and nosniff headers. Personal settings use a full page navigation so
the sidebar, body preferences and avatar update consistently.

## Email identity

Existing accounts remain usable and are not automatically declared verified. Matching
verified_email and current user.email plus a verification timestamp are required for
the verified badge. Open signup confirmation establishes that proof; possession of
an invitation that might have been copied manually does not.

Email changes require the current password and a 24-hour, random, one-use confirmation
sent to the new address. GET displays the request, POST confirms with CSRF protection
and authentication as the owning user. Address and password snapshots invalidate stale
requests. Sending/retrying does not change the current address. Delivery failures,
pending/cancelled/expired states and rate limits have explicit handling. Only digests
of personal email confirmation tokens are stored; tokens and raw session keys never
enter application audit details.

Supported identity entry points serialize email uniqueness checks using account_email_lock:
signup, signup confirmation, invitations, admin forms and personal confirmation. SMTP
delivery happens outside that lock. Existing duplicate addresses are not silently
merged or edited. Confirmation rechecks ownership and uniqueness under the lock.

Public signup resend has a generic response and only serves inactive, membership-free
accounts that have never logged in and are explicitly marked signup_pending. It cannot reactivate a suspended member.
Resending rotates the generation and invalidates previous links. Unexpired, unused legacy signup links
without the new email/generation binding preserve activation and the original organization name,
but do not mark the current email verified. Resending invalidates those older links; the error
page points to resend.
The migration recognizes historical pending registrations by inactive status, no previous login, no membership
and an existing terms acceptance. Historical organization names absent from stored signup
data cannot be recovered from an old email link; resend falls back to the username.

SMTP uses the existing Hub configuration. No test mail is sent to production recipients.
The installation's ADMIN_EMAIL only bootstraps a new admin; changing an existing email
from environment now requires explicit ADMIN_RESET_EMAIL=true. ADMIN_RESET_PASSWORD
retains its existing explicit reset behavior.

## Sessions

UserSession maps public UUIDs to private Django session keys. The listing is user-scoped,
paginated, and displays declared browser/system, recorded IP, discovery/login time and
activity (updated at most once every five minutes). These are not a geographic location
or an exact record of every request. Migration discovers valid, unexpired existing sessions
without changing their data, keys or expiry. Those entries distinguish discovery from
an unknown login time. Revoked entries remain as tombstones; their lifetime/cleanup can
be folded into the installation's personal-data retention policy separately.

Revocation requires the current password and blocks the next request even if Redis still
contains the session or an in-flight request re-saves it. The configured Django SessionStore
is also asked to delete it, invalidating cached_db rather than deleting only the database
row. Current-session revocation uses Logout; the bulk action excludes the current key.
Password changes invalidate all tracked old sessions; the personal password flow rotates
the current Django session and preserves it. Password-reset/admin-password changes are
covered by the same signal. A confirmed email change ends the other Hub sessions.
No game session, agent token or manual-control session is modified by these actions.

Tests cover ownership, CSRF, expiry/reuse, failed mail, signup suspension, avatar normalization,
cached session resurrection, discovery, password rotation and email uniqueness concurrency.
The concurrent uniqueness test requires MariaDB row locks and runs in CI. Browser regression
renders migrated isolated fixtures, with desktop/mobile checks and keyboard navigation.
