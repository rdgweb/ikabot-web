# Access policy

The platform root is the active Django superuser. Identity/password administration stays
root-only. Configurable roles grant explicit `view`/`manage` capabilities, scoped either
to the platform or to one organization. They never grant superuser/staff flags.

`catalog.py` maps route families and methods to capabilities. Add a mapping and a
denial test when introducing a protected route family. Both the existing view guards
and the capability middleware apply. Side-effecting operations must use unsafe HTTP
methods; creation/edit/control forms also require management permission on GET.

The migration creates immutable default roles and changes no people, memberships,
agents, jobs or plan states. Legacy staff and membership roles remain effective until
the first confirmed custom grant for that person and scope. `AccessMode` records that
transition permanently: expiring/revoking all grants does not restore legacy rights.
The protected "Sem acesso" default can explicitly replace legacy rights with none.
An organization role cannot remove rights still granted by global legacy access;
its preview discloses those remaining permissions.

All supported IAM and member changes share a database row lock. Preview tokens are
actor/scope bound, signed, expire in 15 minutes and include a database revision and
the target's effective permissions. Confirmation recomputes authorization and owner
invariants under the lock. Successful writes invalidate the preview. This conservative
revision also invalidates unrelated pending previews when another access edit occurs.

Delegation checks both existing and proposed role permissions against the actor's
permissions. Custom grants require membership in their organization. Removing that
membership immediately invalidates them. Ownership belongs to a legacy owner or the
immutable permanent Owner role; no custom role or expiring grant can become the last
owner. Deactivation/deletion of the last owner is protected in identity administration.

Decisions are cached only within an HTTP request and cleared after a confirmed mutation.
History stores actor labels and before/after snapshots independently of grant deletion.
The organization selector validates membership on every request and never relocates data.

Hub tests run on SQLite and MariaDB; the concurrent last-owner confirmation test runs
only with real row locks (MariaDB in CI). Browser tests render isolated, migrated fixture
data and make no calls to game accounts or production infrastructure.
