"""Persistent, bounded button/dropdown role menus with fresh permission checks."""

import discord
from .reaction_roles import role_error


def validate_menu(data):
    if type(data) is not dict or set(data) != {"title","description","style","min_values","max_values","options"}:
        raise ValueError("Invalid role menu.")
    for key,limit in (("title",100),("description",1000)):
        if type(data[key]) is not str or not 1 <= len(data[key].strip()) <= limit:
            raise ValueError(f"{key.title()} needs 1–{limit} characters.")
    options = data["options"]
    if data["style"] not in ("buttons","select") or type(options) is not list or not 1 <= len(options) <= 25:
        raise ValueError("Choose buttons or a dropdown, with 1–25 options.")
    low,high = data["min_values"],data["max_values"]
    if type(low) is not int or type(high) is not int or not 0 <= low <= high or not 1 <= high <= len(options):
        raise ValueError("Selection limits must fit the number of menu options.")
    seen = set()
    for option in options:
        if type(option) is not dict or set(option) != {"role_id","label","description"}:
            raise ValueError("Invalid menu option.")
        role_id = option["role_id"]
        if type(role_id) is not int or not 0 < role_id < 2**63 or role_id in seen:
            raise ValueError("Choose each role once.")
        seen.add(role_id)
        if type(option["label"]) is not str or not 1 <= len(option["label"].strip()) <= 80 or type(option["description"]) is not str or len(option["description"]) > 100:
            raise ValueError("Options need labels of 1–80 characters and descriptions up to 100 characters.")


class RoleMenuView(discord.ui.View):
    def __init__(self, manager, row, disabled=False):
        super().__init__(timeout=None)
        data = row["data"]
        if data["style"] == "select":
            select = discord.ui.Select(custom_id=f"espresso:roles:{row['id']}:select",placeholder="Choose your community roles",
                min_values=data["min_values"],max_values=data["max_values"],disabled=disabled,
                options=[discord.SelectOption(label=option["label"],description=option["description"] or None,value=str(option["role_id"])) for option in data["options"]])
            async def choose(interaction):
                await manager.handle(interaction,row["id"],selected=[int(value) for value in select.values])
            select.callback = choose
            self.add_item(select)
        else:
            for index,option in enumerate(data["options"]):
                button = discord.ui.Button(label=option["label"],custom_id=f"espresso:roles:{row['id']}:{index}",style=discord.ButtonStyle.secondary,row=index//5,disabled=disabled)
                async def choose(interaction,role_id=option["role_id"]):
                    await manager.handle(interaction,row["id"],toggle=role_id)
                button.callback = choose
                self.add_item(button)

    async def on_error(self, interaction, error, item):
        text = "The role change could not be confirmed. Check your roles before trying again."
        if interaction.response.is_done():
            await interaction.followup.send(text,ephemeral=True)
        else:
            await interaction.response.send_message(text,ephemeral=True)


class RoleMenuManager:
    def __init__(self, bot):
        self.bot,self.store = bot,bot.store

    async def restore_views(self):
        for row in await self.store.menus():
            if row["status"] == "active" and row["message_id"]:
                self.bot.add_view(RoleMenuView(self,row),message_id=row["message_id"])

    async def checked_roles(self, guild, data, actor=None):
        validate_menu(data)
        settings = await self.store.settings(guild.id)
        roles = {role.id:role for role in await guild.fetch_roles()}
        for option in data["options"]:
            role = roles.get(option["role_id"])
            error = role_error(role,guild,guild.me,actor)
            if error:
                raise ValueError(error.replace("Reaction roles","Role menus").replace("reaction role","menu role"))
            if settings.screening["enabled"] and role.id == settings.screening["verified_role_id"]:
                raise ValueError("A role menu cannot grant the verified access role.")
        return roles,settings

    async def publish(self, guild, actor, channel_id, data, source):
        from .community import manager
        manager(actor)
        await self.checked_roles(guild,data,actor)
        channel = await self.bot.community_manager.post_channel(guild,channel_id)
        if not channel.permissions_for(actor).view_channel or not channel.permissions_for(actor).send_messages:
            raise ValueError("Choose a channel you can view and post in.")
        menu_id,created = await self.store.begin_menu(guild.id,actor.id,channel_id,data,source)
        if not created:
            return await self.store.menu(guild.id,menu_id)
        row = await self.store.menu(guild.id,menu_id)
        embed = discord.Embed(title=data["title"],description=data["description"],color=0xD8A7B1)
        if data["style"] == "buttons":
            for option in data["options"]:
                if option["description"]:
                    embed.add_field(name=option["label"],value=option["description"],inline=False)
        embed.set_footer(text=f"Choose {data['min_values']}–{data['max_values']} roles · menu #{menu_id}")
        try:
            message = await channel.send(embed=embed,view=RoleMenuView(self,row),allowed_mentions=discord.AllowedMentions.none())
            await self.store.finish_menu(guild.id,menu_id,"active",message.id)
        except BaseException:
            await self.store.finish_menu(guild.id,menu_id,"uncertain")
            raise
        await self.bot.log_action(guild,"Role menu published",f"Menu #{menu_id} · channel {channel_id} · moderator {actor.id}.")
        return await self.store.menu(guild.id,menu_id)

    async def disable(self, guild, actor, menu_id):
        from .community import manager
        manager(actor)
        if not actor.guild_permissions.manage_roles:
            raise ValueError("Manage Roles is required to disable a role menu.")
        row = await self.store.menu(guild.id,menu_id)
        if row is None:
            raise ValueError("That menu is not in this server.")
        # Disable server-side before editing the public message; stale buttons can no longer grant roles.
        await self.store.finish_menu(guild.id,menu_id,"disabled")
        if row["message_id"]:
            try:
                channel = await guild.fetch_channel(row["channel_id"])
                message = await channel.fetch_message(row["message_id"])
                await message.edit(view=RoleMenuView(self,row,disabled=True))
            except discord.HTTPException:
                return "Menu disabled. Its message could not be updated, but the controls no longer grant roles."
        await self.bot.log_action(guild,"Role menu disabled",f"Menu #{menu_id} · moderator {actor.id}.")
        return "Menu disabled. Existing member roles remain in place."

    async def handle(self, interaction, menu_id, *, selected=None, toggle=None):
        await interaction.response.defer(ephemeral=True)
        try:
            if not interaction.guild:
                raise ValueError("Use this menu in its server.")
            async with self.bot.member_lock(interaction.guild_id,interaction.user.id):
                row = await self.store.menu(interaction.guild_id,menu_id)
                if row is None or row["status"] != "active" or row["message_id"] != interaction.message.id:
                    raise ValueError("This menu is no longer active.")
                roles,settings = await self.checked_roles(interaction.guild,row["data"])
                member = await interaction.guild.fetch_member(interaction.user.id)
                if member.bot or getattr(member,"pending",False):
                    raise ValueError("Complete Discord's member screening first.")
                actual = {role.id for role in member.roles}
                if settings.screening["enabled"] and settings.screening["verified_role_id"] not in actual:
                    raise ValueError("Complete /verify before choosing community roles.")
                choices = {option["role_id"] for option in row["data"]["options"]}
                owned = (await self.store.menu_grants(interaction.guild_id,menu_id,member.id)) & actual
                current = actual & choices
                manual = current-owned
                if toggle is not None:
                    if toggle not in choices:
                        raise ValueError("Unknown menu choice.")
                    if toggle in current:
                        desired = current-{toggle}
                    elif row["data"]["max_values"] == 1:
                        desired = {toggle}
                    else:
                        desired = current|{toggle}
                else:
                    desired = set(selected or [])
                if not desired <= choices or not manual <= desired:
                    raise ValueError("This menu cannot remove a role granted outside this menu. Ask staff to change it.")
                if not row["data"]["min_values"] <= len(desired) <= row["data"]["max_values"]:
                    raise ValueError("Your selection is outside this menu's allowed number of roles.")
                add,remove = desired-current,owned-desired
                try:
                    if add:
                        await member.add_roles(*(roles[role_id] for role_id in add),reason=f"Espresso role menu #{menu_id}",atomic=True)
                    if remove:
                        await member.remove_roles(*(roles[role_id] for role_id in remove),reason=f"Espresso role menu #{menu_id}",atomic=True)
                except discord.HTTPException:
                    # Reconcile recorded ownership against Discord instead of assuming a partial request failed entirely.
                    refreshed = await interaction.guild.fetch_member(member.id)
                    present = {role.id for role in refreshed.roles}
                    await self.store.save_menu_grants(interaction.guild_id,menu_id,member.id,(owned|add)&present)
                    raise ValueError("Discord could only partially confirm the change. Check your roles and retry; staff can help resolve a selection limit.") from None
                await self.store.save_menu_grants(interaction.guild_id,menu_id,member.id,(owned|add)-remove)
                await self.bot.log_action(interaction.guild,"Role menu selection",f"Menu #{menu_id} · member {member.id} · added {len(add)} · removed {len(remove)}.")
            await interaction.followup.send("Your community roles are up to date.",ephemeral=True)
        except (ValueError,discord.HTTPException) as exc:
            await interaction.followup.send(str(exc) if isinstance(exc,ValueError) else "Discord could not confirm your roles. Check them before retrying.",ephemeral=True)
