# Trash, archive and audit history: the rules

These rules apply to every part of the APS dashboard, now and for every feature we add later.
Code: `apps/records`. Dashboard pages: **Administration → Trash** and **Administration → Audit history**.

## Trash

Deleting something in the dashboard never deletes it straight away. It goes to the **Trash**.

| Who | Sees in the Trash | Can restore |
|---|---|---|
| Staff member with the **Trash** permission (`trash.view`) | Only the items **they deleted themselves**, and only of the types they're allowed to view (e.g. no deleted reports without "View reports") | Those items |
| Staff member without the **Trash** permission | Nothing (no Trash page) | Nothing |
| **Superuser** (no permission needed) | **Everyone's** deleted items | **Everything** |
| Client | Nothing | Nothing |

- **Nobody can delete anything for good by hand.** Not staff, not superusers, not in the Django admin.
- Items are **deleted for good automatically after 90 days** in the trash (daily job at 03:00). The audit history records this as "System".
- The **Trash** permission is **on by default** for every staff account. A superuser can switch it off per person on **Administration → Staff accounts**.
- A restored item goes back exactly where it was, with everything it had.
- Clicking an item in the Trash opens a **preview** in that type's own design (a report's site figures, an estimate's proposal pages, a post or project as on the website, a category's trashed posts), with its history, before restoring. The preview follows the same rules: you only preview what you could restore.
- An item can't be restored while its parent is still in the trash (a project whose service is in the trash, a blog post whose category is in the trash). Restore the parent first.
- Moving a **service** to the trash takes its projects with it; restoring the service brings those projects back.
- A **solar report** can't be restored if a newer report now covers the same client and period.
- In the **Django admin**, trashable items have a "Not in trash / In trash / All" filter and **Move to trash** / **Restore** actions. Django's normal delete is switched off.

What goes to the trash today: jobs, solar reports, solar estimates, services, projects, blog posts, blog categories.

- A **job's** number (APS-###) stays taken while it's in the trash, so it's never reused. Its comments go and come back with it; comments themselves are never deleted (the author can edit their own).

- A **solar estimate's** quotation number stays taken while it's in the trash, so it's never reused and a restore never clashes.

**Tickets can't be deleted at all** — not by clients, staff, superusers or in the Django admin. Close them with a status instead.

## Archive

**Archive** means "finished, keep forever, hide from the everyday lists". It is **not** the trash:

| | Archive | Trash |
|---|---|---|
| Meaning | Finished, kept for the record | Deleted by mistake or not wanted |
| Deleted for good? | **Never** | Automatically after 90 days |
| Brought back by | **Unarchive**, any time | Restore (only your own, unless superuser) |
| Still opens, can be commented on / chatted in | Yes | No |

What can be archived: **jobs, quote requests (general, solar and container), solar estimates and tickets.** Each list shows active items by default, with an **Active | Archived** switch and an **Include archived** search option.

**Automatic archiving** happens in the same save that finishes the item, and the dashboard shows a notice with **Undo**:

- **Jobs:** status Done or Invoiced **and** payment Paid. "No charge" jobs are never archived automatically.
- **Tickets:** status Completed or Cancelled.
- **Quote requests and solar estimates:** never automatically, only by hand.

The edge cases:

- Only a save that **takes an item into** its rule archives it. Saves that leave status and payment alone, new comments and chat messages never archive or unarchive anything.
- An item **archived automatically** that is changed so it no longer meets the rule (payment back to Unpaid, a ticket reopened) **comes back to Active** in that same save, with its own history entry.
- An item **archived by hand** stays archived whatever is changed.
- An item someone **unarchived** (by hand or with Undo) while it still meets the rule stays active. It is archived again only if it leaves the rule and meets it again (e.g. Unpaid, then Paid again).
- Imports (the Operations sheet), the WhatsApp jobs and management commands never archive automatically.
- Deleting an archived job or estimate puts it in the trash as usual; restoring it keeps it archived.
- Items finished **before** the archive existed are archived once with `python manage.py archive_finished` (`--dry-run` counts first). It skips anything someone has unarchived.

**Permissions:** archiving and unarchiving by hand (one item or several at once) needs `jobs.archive`, `tickets.archive`, `quotes.archive` or `estimates.archive`. They are **off by default** for new staff. When they were added, staff who could already edit that section got them (for quote requests: who could view them). Automatic archiving needs no permission: it follows from an edit the person was allowed to make. **Clients** see their own archived tickets (when, and why if automatic, but not who) and can't archive or unarchive.

**History:** archiving and unarchiving are recorded as their own "Archived" / "Unarchived" entries ("Archived automatically: invoiced and paid"), never as field changes.

## Audit history

Every action in the dashboard is recorded: who did it, when, to which item, and the old → new values. **Reading/viewing is not recorded.**

There are two pages (sidebar sub-menu):

- **Staff & admin** (the default): everything staff and admins did, plus what the system did automatically.
- **Clients**: everything clients did (sign-ups, sign-ins, tickets, chat messages, sites, account changes, subscription cancellations).

| Who | Can see |
|---|---|
| **Superuser** (no permission needed) | Both audit history pages |
| Staff member with **View the full audit history** (`audit.view_all`) | Both audit history pages |
| Any other staff member | No audit history pages. They can still see **one item's own history** (e.g. a ticket's history) if they can view that item's section |
| Client | Nothing |

- **View the full audit history** is **off by default**. A superuser ticks it per person on **Administration → Staff accounts**.
- **Audit history can never be edited or deleted** — not by staff, not by superusers, not in the Django admin. The database itself refuses changes (PostgreSQL trigger).
- The only exception is local demo data: `python manage.py seed_audit_demo --clear` removes rows it added itself, and only runs when `DEBUG=True`.

## Every new feature must

1. Put deleted items in the trash: make the model a `TrashableModel` (with `trash_section` and `trash_label`) and call `obj.trash(request.user)` instead of `obj.delete()`.
   Give it a Trash preview: `trash_preview(request)` on the model and a design in the dashboard's `components/dashboard/TrashPreviews.tsx`.
2. Record every create, edit and other action in the audit history (`apps.records.audit`: `log_create`, `log_update`, `log`), for staff and client actions alike.
3. Keep using `Model.objects` for normal lists (it hides trashed items) and `Model.all_objects` only where trashed items must be seen.
4. If finished items should leave the everyday lists, make the model an `ArchivableModel` too and use `apps.records.archive` (`filter_archived` in the list, `ArchiveView` / `BulkArchiveView`, and `auto_archive_note` + `apply_auto_archive` for an automatic rule).
