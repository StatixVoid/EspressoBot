from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord

from espresso.bot import Espresso
from espresso.reaction_roles import emoji_key, mapping_key, role_error, validate_mappings


class Role:
    def __init__(self,id,position=1,**permissions):
        self.id,self.position,self.managed,self.name = id,position,False,f"Role {id}"
        self.permissions = SimpleNamespace(**permissions)
    def is_default(self):
        return self.id == 1
    def __le__(self,other):
        return self.position <= other.position


class ReactionRoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Espresso(":memory:")
        self.bot._connection.user = SimpleNamespace(id=99)
        await self.bot.store.open()
        self.bot.log_action = AsyncMock()
        self.role = Role(20)
        self.me = SimpleNamespace(top_role=Role(99,10),guild_permissions=SimpleNamespace(manage_roles=True))
        self.actor = SimpleNamespace(id=77,top_role=Role(77,8),guild_permissions=SimpleNamespace(manage_roles=True))
        self.member = SimpleNamespace(id=10,bot=False,roles=[],add_roles=AsyncMock(),remove_roles=AsyncMock())
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 5
        self.channel.permissions_for.return_value = SimpleNamespace(view_channel=True,read_message_history=True,add_reactions=True)
        self.message = SimpleNamespace(id=50,add_reaction=AsyncMock())
        self.channel.fetch_message = AsyncMock(return_value=self.message)
        self.guild = SimpleNamespace(id=1,owner_id=100,me=self.me,emojis=[],
                                      fetch_roles=AsyncMock(return_value=[self.role]),
                                      fetch_channels=AsyncMock(return_value=[self.channel]),
                                      fetch_member=AsyncMock(return_value=self.member))
        self.bot.get_guild = Mock(return_value=self.guild)
        self.mapping = {"channel_id":5,"message_id":50,"emoji":"☕","role_id":20,"remove_on_unreact":True}
        self.payload = SimpleNamespace(guild_id=1,channel_id=5,message_id=50,user_id=10,emoji="☕")
        await self.bot.store.update(1,reaction_roles_enabled=True,reaction_roles=[self.mapping])

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_grants_and_removes_only_its_own_grants(self):
        await self.bot.on_raw_reaction_add(self.payload)
        self.member.add_roles.assert_awaited_once()
        self.assertTrue(await self.bot.store.role_grant(1,mapping_key(self.mapping),10))
        self.member.roles = [self.role]
        await self.bot.on_raw_reaction_remove(self.payload)
        self.member.remove_roles.assert_awaited_once()
        self.assertFalse(await self.bot.store.role_grant(1,mapping_key(self.mapping),10))

    async def test_preexisting_role_is_never_revoked(self):
        self.member.roles = [self.role]
        await self.bot.on_raw_reaction_add(self.payload)
        await self.bot.on_raw_reaction_remove(self.payload)
        self.member.add_roles.assert_not_awaited()
        self.member.remove_roles.assert_not_awaited()

    async def test_sensitive_roles_are_rejected_at_runtime(self):
        self.role.permissions.administrator = True
        await self.bot.on_raw_reaction_add(self.payload)
        self.member.add_roles.assert_not_awaited()

    async def test_removed_reaction_can_leave_role_when_configured(self):
        self.mapping["remove_on_unreact"] = False
        await self.bot.store.update(1,reaction_roles=[self.mapping])
        await self.bot.on_raw_reaction_add(self.payload)
        self.member.roles = [self.role]
        await self.bot.on_raw_reaction_remove(self.payload)
        self.member.remove_roles.assert_not_awaited()

    async def test_disabled_or_unrelated_reaction_is_ignored(self):
        self.payload.message_id = 51
        await self.bot.on_raw_reaction_add(self.payload)
        self.payload.message_id = 50
        await self.bot.store.update(1,reaction_roles_enabled=False)
        await self.bot.on_raw_reaction_add(self.payload)
        self.member.add_roles.assert_not_awaited()

    async def test_mapping_deletion_prunes_ownership_without_removing_roles(self):
        await self.bot.on_raw_reaction_add(self.payload)
        await self.bot.store.update(1,reaction_roles=[])
        self.assertFalse(await self.bot.store.role_grant(1,mapping_key(self.mapping),10))
        self.member.remove_roles.assert_not_awaited()

    async def test_validates_message_and_seeds_emoji(self):
        prepared = await self.bot.reaction_manager.validate(self.guild,self.actor,[self.mapping])
        self.message.add_reaction.assert_not_awaited()
        self.assertEqual(await self.bot.reaction_manager.seed(prepared),[])
        self.message.add_reaction.assert_awaited_once_with("☕")

    async def test_actor_and_bot_hierarchy_and_privileges(self):
        self.actor.guild_permissions.manage_roles = False
        with self.assertRaisesRegex(ValueError,"Manage Roles"):
            await self.bot.reaction_manager.validate(self.guild,self.actor,[self.mapping])
        self.actor.guild_permissions.manage_roles = True
        self.role.position = 9
        with self.assertRaisesRegex(ValueError,"Your highest role"):
            await self.bot.reaction_manager.validate(self.guild,self.actor,[self.mapping])
        self.role.position = 10
        self.assertIn("Espresso",role_error(self.role,self.guild,self.me))

    async def test_discord_failure_is_reported_without_tracking_a_grant(self):
        self.member.add_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403,reason="Forbidden"),"Forbidden")
        await self.bot.on_raw_reaction_add(self.payload)
        self.assertFalse(await self.bot.store.role_grant(1,mapping_key(self.mapping),10))
        self.assertEqual(self.bot.log_action.await_args.args[1],"Reaction role failed")

    async def test_duplicate_roles_and_triggers_are_rejected(self):
        for second in ({**self.mapping,"message_id":60},{**self.mapping,"role_id":21}):
            with self.assertRaises(ValueError):
                validate_mappings([self.mapping,second])
        self.assertEqual(emoji_key("☕"),emoji_key("☕️"))
