# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""Ratatosk on IntelliCat, stage 7b: the groups themselves (docs/usage.md; ChatRepository.kt, GroupKeyRepository.kt and E2E_SPEC.md section 5 are the references).

What a person can do here: see who is in a chat (admins, members, the ones only invited), search the directory of people and of groups, start a chat with a person (the DM is the group whose
id is the hash of the pair), create a group, add a member, remove one, leave, delete a group (admins), rename it (admins), invite somebody who has no account yet, and accept the invitations
that wait for this account. The rules are the server's (GroupManager): only an admin adds, renames or deletes; an admin removes anybody; anybody removes themselves. This module never
asks around them: a refusal of the server is shown as it is.

Encryption goes with every change of the membership (spec section 5), because the console is a member that holds the keys:
* a new group is encrypted from the start (a key sealed to every member that has a key record);
* a member added gets every key sealed to them, so they can read the history (`Chats.sync_member_keys`);
* a member removed, and a member who leaves, means a NEW key sealed to the members that remain, which the new messages use (`Chats.rotate`). The one who leaves is rotated out BEFORE the
  server is asked to remove them (the phones rotate only on a removal): afterwards the console is no longer a member and could not write the files of the group.
A member without a key record gets no key until they have one (spec 5): they see nothing new, and the next active member that holds the keys seals them in.
"""
import json, re, time, uuid

from ratatosk_chat import day  # noqa: F401  (kept next to the chat module's helpers)

EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
INVITES_EVERY_S = 60


class GroupError(Exception):
    def __init__(self, message, code="group"):
        super().__init__(message)
        self.code = code


def clean_email(email):
    e = str(email or "").strip().lower()
    if not EMAIL.match(e):
        raise GroupError("that is not an e-mail address", "bad")
    return e


def clean_name(name):
    n = " ".join(str(name or "").split())
    if not n:
        raise GroupError("a group needs a name", "bad")
    return n[:60]


class Groups:
    def __init__(self, rt, chats):
        self.rt, self.chats = rt, chats
        self._invites_at = 0.0

    # ---- helpers ----------------------------------------------------------------------------------------------------------
    def _me(self):
        a = self.rt._account()
        if not a:
            raise GroupError("not signed in", "not-signed-in")
        return a["email"]

    def _ok(self, resp, what):
        if resp.get(".status") != "ok":
            reason = str(resp.get(".reason") or resp.get(".status") or "refused")
            raise GroupError(f"{what}: {reason}", "refused")
        return resp

    def _group(self, gid):
        r = self.rt.send_auth({"command": "group:get", "group_id": gid})
        g = r.get("group") if r.get(".status") == "ok" else None
        if not isinstance(g, dict):
            raise GroupError("no such group", "not-found")
        return g

    def _name_of(self, email):
        """A person's name from the profile (asked once, then kept), the e-mail when there is none."""
        with self.rt.lock:
            row = self.rt.db.execute("SELECT name FROM users WHERE email=?", (email,)).fetchone()
        if row:
            return row[0] or email
        name = ""
        try:
            from ratatosk import profile_hash
            v = self.rt.send_auth({"command": "var:getAll", "path": f"users/{profile_hash(email)}"}).get(".value") or {}
            name = " ".join(x for x in ((v.get("firstName") or "").strip(), (v.get("lastName") or "").strip()) if x)
        except Exception:  # noqa: BLE001
            pass
        with self.rt.lock:
            self.rt.db.execute("INSERT OR REPLACE INTO users (email, name) VALUES (?,?)", (email, name or email))
            self.rt.db.commit()
        return name or email

    def _forget_chat(self, gid):
        """The chat is no longer ours (we left, or it was deleted): its rows go from the local store."""
        with self.rt.lock:
            for sql in ("DELETE FROM chats WHERE id=?", "DELETE FROM messages WHERE chat=?", "DELETE FROM events WHERE chat=?", "DELETE FROM chat_state WHERE chat=?"):
                self.rt.db.execute(sql, (gid,))
            self.rt.db.commit()

    def _refresh(self):
        """Pull the chat list again (a new group appears, a left one goes)."""
        try:
            self.rt.sync_now()
        except Exception as e:  # noqa: BLE001
            self.rt.log(f"ratatosk: the list could not be refreshed after a change of a group: {e}")

    def _invited(self, gid):
        st, text = self.chats.read_file(f"chats/{gid}/invited.json")
        if st != "found":
            return []
        try:
            v = json.loads(text)
            return [str(x).strip().lower() for x in v if isinstance(x, str)] if isinstance(v, list) else []
        except ValueError:
            return []

    # ---- reading ----------------------------------------------------------------------------------------------------------
    def info(self, gid):
        """Who is in a chat: {id, name, is_group, me, me_admin, people: [{email, name, role}], invited: [emails]}."""
        me = self._me()
        g = self._group(gid)
        people = []
        for role, key in (("admin", "admins"), ("member", "members"), ("observer", "observers")):
            for e in (g.get(key) or []):
                if isinstance(e, str) and all(p["email"] != e.lower() for p in people):
                    people.append({"email": e.lower(), "name": self._name_of(e.lower()), "role": role})
        with self.rt.lock:
            row = self.rt.db.execute("SELECT name, is_group FROM chats WHERE id=?", (gid,)).fetchone()
        # the name the group has on the server (the list shows a group of exactly two people as the phones do: the other person and "(dialog ...)", which hides this name)
        return {"id": gid, "name": ((g.get("meta") or {}).get("name") or (row[0] if row else "") or gid), "is_group": bool(row[1]) if row else True, "me": me,
                "me_admin": me in [str(x).lower() for x in (g.get("admins") or [])], "people": people, "invited": self._invited(gid)}

    def search_users(self, query):
        me = self._me()
        q = str(query or "").strip()
        if len(q) < 2:
            return []
        r = self.rt.send_auth({"command": "chat:search_users", "query": q, "from": me, "limit": 20})
        if r.get(".status") != "ok":
            return []
        return [{"email": str(o.get("email", "")).lower(), "name": " ".join(x for x in (o.get("firstName", ""), o.get("lastName", "")) if x) or str(o.get("email", ""))}
                for o in (r.get("results") or []) if o.get("email")]

    def search_groups(self, query):
        me = self._me()
        q = str(query or "").strip()
        if len(q) < 2:
            return []
        r = self.rt.send_auth({"command": "chat:search_groups", "query": q, "from": me, "limit": 20})
        if r.get(".status") != "ok":
            return []
        return [{"id": o.get("groupId"), "name": o.get("name"), "count": int(o.get("memberCount") or 0), "member": bool(o.get("isMember"))} for o in (r.get("results") or []) if o.get("groupId")]

    # ---- making chats -----------------------------------------------------------------------------------------------------
    def create_dm(self, email):
        """The chat with one person: the group whose id is the hash of the pair; an existing one is used as it is."""
        from ratatosk import direct_chat_id
        me, other = self._me(), clean_email(email)
        if other == me:
            raise GroupError("that is you", "bad")
        gid = direct_chat_id(me, other)
        try:
            self._group(gid)
        except GroupError as e:
            if e.code != "not-found":
                raise
            self._ok(self.rt.send_auth({"command": "group:create", "group_id": gid, "name": other, "description": "", "creator": me, "members": [other]}), "could not start the chat")
        self._refresh()
        return gid

    def create_group(self, name, members=()):
        """A new group, encrypted from the start; the others are added with `add_member`."""
        me, name = self._me(), clean_name(name)
        others = [m for m in dict.fromkeys(clean_email(m) for m in members) if m != me]
        gid = uuid.uuid4().hex[:12]
        self._ok(self.rt.send_auth({"command": "group:create", "group_id": gid, "name": name, "description": "", "creator": me, "members": others}), "could not create the group")
        try:
            self.chats.ensure_enabled(gid, others + [me])
        except Exception as e:  # noqa: BLE001
            self.rt.log(f"ratatosk: encryption of the new group {gid} was not enabled yet (the first message does it): {e!r}")
        self._refresh()
        return gid

    # ---- changing a group ---------------------------------------------------------------------------------------------------
    def add_member(self, gid, email):
        """Add a person. One who has no account is invited instead (what the web client does): the invitation waits for them. -> "added" | "invited"."""
        me, other = self._me(), clean_email(email)
        g = self._group(gid)
        if other in [str(x).lower() for k in ("admins", "members", "observers") for x in (g.get(k) or [])]:
            raise GroupError("already in the group", "bad")
        r = self.rt.send_auth({"command": "group:addMember", "group_id": gid, "user": other, "role": "members", "requester": me})
        if r.get(".status") != "ok":
            reason = str(r.get(".reason") or "")
            if re.search(r"not found|no such|does not exist|unknown user|not registered", reason, re.I) and "group" not in reason.lower():
                self.invite(gid, other)
                return "invited"
            raise GroupError(f"could not add: {reason or r.get('.status')}", "refused")
        try:
            self.chats.sync_member_keys(gid, [other])        # every key sealed to them: they can read the history (spec 5)
        except Exception as e:  # noqa: BLE001
            self.rt.log(f"ratatosk: keys for the new member of {gid} were not sealed yet: {e!r}")
        return "added"

    def invite(self, gid, email):
        """Somebody without an account: the chat's invited list and a pointer under their hash, which every client reads at its sync (web: addMember; Android: checkAndAcceptInvite)."""
        from ratatosk import profile_hash
        other = clean_email(email)
        invited = self._invited(gid)
        if other not in invited:
            self.chats.write_file(f"chats/{gid}/invited.json", json.dumps(invited + [other]))
        path = f"group_invites/{profile_hash(other)}.json"
        st, text = self.chats.read_file(path)
        ptr = []
        if st == "found":
            try:
                ptr = [x for x in json.loads(text) if isinstance(x, str)]
            except ValueError:
                ptr = []
        if gid not in ptr:
            self.chats.write_file(path, json.dumps(ptr + [gid]))

    def remove_member(self, gid, email):
        me, other = self._me(), clean_email(email)
        if other == me:
            return self.leave(gid)
        self._ok(self.rt.send_auth({"command": "group:removeMember", "group_id": gid, "user": other, "requester": me}), "could not remove")
        remaining = [p["email"] for p in self.info(gid)["people"] if p["email"] != other]
        self.chats.rotate(gid, remaining)                    # the new messages are sealed to the ones who remain only (spec 5)

    def leave(self, gid):
        me = self._me()
        try:
            remaining = [p["email"] for p in self.info(gid)["people"] if p["email"] != me]
            self.chats.rotate(gid, remaining, keep_me=False)  # while we are still a member who can write the files of the group; the new key is not for us: we are leaving
        except Exception as e:  # noqa: BLE001
            self.rt.log(f"ratatosk: the key of {gid} was not rotated before leaving: {e!r}")
        self._ok(self.rt.send_auth({"command": "group:removeMember", "group_id": gid, "user": me, "requester": me}), "could not leave")
        self._forget_chat(gid)
        self._purge_own_invites(gid, me)
        self._refresh()

    def delete_group(self, gid):
        me = self._me()
        self._ok(self.rt.send_auth({"command": "group:delete", "group_id": gid, "requester": me}), "could not delete")
        self._forget_chat(gid)
        self._refresh()

    def rename(self, gid, name):
        me, name = self._me(), clean_name(name)
        self._ok(self.rt.send_auth({"command": "group:updateMeta", "group_id": gid, "requester": me, "name": name}), "could not rename")
        with self.rt.lock:
            self.rt.db.execute("UPDATE chats SET name=?, server_name=? WHERE id=? AND is_group=1", (name, name, gid))
            self.rt.db.commit()

    # ---- invitations that wait for this account ---------------------------------------------------------------------------------
    def _purge_own_invites(self, gid, me):
        """After leaving: no stale invitation may bring the group back by itself at the next sync."""
        from ratatosk import profile_hash
        inv = self._invited(gid)
        if me in inv:
            self.chats.write_file(f"chats/{gid}/invited.json", json.dumps([x for x in inv if x != me]))
        path = f"group_invites/{profile_hash(me)}.json"
        st, text = self.chats.read_file(path)
        if st == "found":
            try:
                ptr = [x for x in json.loads(text) if isinstance(x, str)]
                if gid in ptr:
                    self.chats.write_file(path, json.dumps([x for x in ptr if x != gid]))
            except ValueError:
                pass

    def accept_invites(self, force=False):
        """The pointers under this account's hash name the groups it was invited to: each is accepted (as the phone does, with this account as the requester; a server that still wants
        an admin for that refuses, and the invitation stays) and the pointer list is emptied only when every acceptance worked. At most once a minute."""
        if not force and time.time() - self._invites_at < INVITES_EVERY_S:
            return 0
        self._invites_at = time.time()
        from ratatosk import profile_hash
        me = self._me()
        path = f"group_invites/{profile_hash(me)}.json"
        st, text = self.chats.read_file(path)
        if st != "found":
            return 0
        try:
            gids = [x for x in json.loads(text) if isinstance(x, str)]
        except ValueError:
            return 0
        done = 0
        for gid in gids:
            inv = self._invited(gid)
            if me not in inv:
                done += 1
                continue
            r = self.rt.send_auth({"command": "group:addMember", "group_id": gid, "user": me, "role": "members", "requester": me})
            if r.get(".status") == "ok":
                self.chats.write_file(f"chats/{gid}/invited.json", json.dumps([x for x in inv if x != me]))
                done += 1
            else:
                self.rt.log(f"ratatosk: the invitation to {gid} could not be accepted: {r.get('.reason')}")
        if done == len(gids) and gids:
            self.chats.write_file(path, "[]")
        if done:
            self._refresh()
        return done
