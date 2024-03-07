import json
import logging
from pathlib import Path

import httpx

from ctfd_sdk import settings


class CTFdException(Exception):
    pass


class CtfdConnector:
    def __init__(self, admin_token: str, host: str):
        admin_token = admin_token or settings.CTFD_ADMIN_TOKEN
        assert admin_token is not None, 'To use ctfd you need to define "CTFD_ADMIN_TOKEN"'
        self.admin_token = admin_token
        host = host or settings.CTFD_HOST
        assert host is not None, 'To use ctfd you need to define "CTFD_HOST"'
        self.host = host.strip('/')
        self.logger = logging.getLogger('ctfd')

    def __set_args_kwargs(self, *args, **kwargs):
        url = f'{self.host}/api/v1{args[0]}'
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        kwargs['headers']['Content-Type'] = 'application/json'
        kwargs['headers']['Authorization'] = f'Token {self.admin_token}'
        return (url,) + (args[1:]), kwargs

    def __handle_bad_response(self, func, url, response):
        if response.status_code in [200, 201]:
            return
        msg = f'Unable to get response in method {func.__name__} from url {url}  in CTFd: {response.text}'
        self.logger.warning(msg)
        raise CTFdException(msg)

    def aassign_host(func):
        async def inner(self, *args, **kwargs):
            args, kwargs = self.__set_args_kwargs(*args, **kwargs)
            response = await func(self, *args, **kwargs)  # noqa
            self.__handle_bad_response(func, args[0], response)
            return response

        return inner

    def assign_host(func):
        def inner(self, *args, **kwargs):
            args, kwargs = self.__set_args_kwargs(*args, **kwargs)
            response = func(self, *args, **kwargs)  # noqa
            self.__handle_bad_response(func, args[0], response)
            return response

        return inner

    @aassign_host
    async def aget(self, url, *args, **kwargs):
        async with httpx.AsyncClient() as client:
            response = await client.get(url, *args, **kwargs)
        return response

    @aassign_host
    async def apost(self, url, *args, **kwargs):
        async with httpx.AsyncClient() as client:
            response = await client.post(url, *args, **kwargs)
            # response.status_code = 1
        return response

    @aassign_host
    async def apatch(self, url, *args, **kwargs):
        async with httpx.AsyncClient() as client:
            response = await client.patch(url, *args, **kwargs)
        return response

    @aassign_host
    async def adelete(self, url, *args, **kwargs):
        async with httpx.AsyncClient() as client:
            response = await client.delete(url, *args, **kwargs)
        return response

    @assign_host
    def get(self, url, *args, **kwargs):
        with httpx.Client() as client:
            response = client.get(url, *args, **kwargs)
        return response

    @assign_host
    def post(self, url, *args, **kwargs):
        with httpx.Client() as client:
            response = client.post(url, *args, **kwargs)
        return response

    @assign_host
    def patch(self, url, *args, **kwargs):
        with httpx.Client() as client:
            response = client.patch(url, *args, **kwargs)
        return response

    @assign_host
    def delete(self, url, *args, **kwargs):
        with httpx.Client() as client:
            response = client.delete(url, *args, **kwargs)
        return response


class Storage:
    def __init__(self, storage_path: str | Path):
        storage_path = storage_path or settings.CTFD_STORAGE
        assert storage_path is not None, 'To use ctfd you need to define "CTFD_STORAGE"'
        self.storage_path = Path(storage_path)
        self.logger = logging.getLogger('ctfd')

    def get_storage(self):
        if not self.storage_path.exists():
            with open(self.storage_path, 'w') as f:
                json.dump({'users': {}, 'teams': {}, 'challenges': {}, 'flags': {}}, f)
        with open(self.storage_path) as f:
            return json.load(f)

    def get_field_from_storage(self, field: str, name: str):
        storage = self.get_storage()
        if name not in storage[field]:
            msg = f'{name} not found in storage `{field}`'
            self.logger.warning(msg)
            raise CTFdException(msg)
        return storage[field][name]

    def save_storage(self, storage: dict):
        with open(self.storage_path, 'w') as f:
            json.dump(storage, f)

    def update_storage_field_from_response(self, response, field: str, name: str):
        data = response.json()['data']
        storage = self.get_storage()
        storage[field][name] = {'id': data['id']}
        self.save_storage(storage)

    def exist_in_field(self, field: str, name: str):
        storage = self.get_storage()
        return name in storage[field]

    def delete_storage_field(self, field: str, name: str):
        storage = self.get_storage()
        del storage[field][name]
        self.save_storage(storage)


class CtfdApi:
    def __init__(self, admin_token: str = None, host: str = None, storage_path: str | Path = None):
        self.connector = CtfdConnector(admin_token, host)
        self.storage = Storage(storage_path)
        self.logger = logging.getLogger('ctfd')

    def _create_user_core(self, response, username):
        data = response.json()['data']
        storage = self.storage.get_storage()
        storage['users'][username] = {'id': data['id'], 'team_id': None}
        self.storage.save_storage(storage)
        self.logger.info(f'Agent with username: {username} registered in CTFd')

    def _create_user_request(self, username: str, is_admin=False):
        if self.storage.exist_in_field('users', username):
            self.logger.info(f'User {username} already registered in CTFd')
            raise CTFdException(f'User {username} already registered in CTFd')
        return ('/users',), {
            'json': {
                'banned': False,
                'email': f'{username}@email.com',
                'fields': [],
                'hidden': False,
                'name': username,
                'password': username,
                'type': 'admin' if is_admin else 'user',
                'verified': True,
            }
        }

    def create_user(self, username: str, is_admin=False):
        args, kwargs = self._create_user_request(username, is_admin)
        response = self.connector.post(*args, **kwargs)
        self._create_user_core(response, username)

    async def acreate_user(self, username: str, is_admin=False):
        args, kwargs = self._create_user_request(username, is_admin)
        response = await self.connector.apost(*args, **kwargs)
        self._create_user_core(response, username)

    def _create_team_core(self, response, name):
        self.storage.update_storage_field_from_response(response, 'teams', name)

    def _create_team_request(self, name: str):
        if self.storage.exist_in_field('teams', name):
            self.logger.info(f'Team {name} already registered in CTFd')
            raise CTFdException(f'Team {name} already registered in CTFd')
        return ('/teams',), {
            'json': {
                'banned': False,
                'country': 'CZ',
                'email': f'{name}@copas.cz',
                'fields': [],
                'hidden': False,
                'name': name,
                'password': name,
            }
        }

    def create_team(self, name: str):
        args, kwargs = self._create_team_request(name)
        response = self.connector.post(*args, **kwargs)
        self._create_team_core(response, name)

    async def acreate_team(self, name: str):
        args, kwargs = self._create_team_request(name)
        response = await self.connector.apost(*args, **kwargs)
        self._create_team_core(response, name)

    def _remove_user_from_team_request(self, user_name):
        user = self.storage.get_field_from_storage('users', user_name)
        if user['team_id'] is None:
            self.logger.info(f'User {user_name} not assigned to any team')
            return None, None
        return (f'teams/{user["team_id"]}/members',), {'json': {'user_id': user['id']}}

    def _remove_user_from_team_core(self, user_name):
        storage = self.storage.get_storage()
        storage['users'][user_name]['team_id'] = None
        self.storage.save_storage(storage)

    def remove_user_from_team(self, user_name):
        args, kwargs = self._remove_user_from_team_request(user_name)
        if args is None:
            return
        self.connector.delete(*args, **kwargs)
        self._remove_user_from_team_core(user_name)

    async def aremove_user_from_team(self, user_name):
        args, kwargs = self._remove_user_from_team_request(user_name)
        if args is None:
            return
        await self.connector.adelete(*args, **kwargs)
        self._remove_user_from_team_core(user_name)

    def assign_user2team(self, user_name, team_name):
        user = self.storage.get_field_from_storage('users', user_name)
        team = self.storage.get_field_from_storage('teams', team_name)
        if user['team_id'] == team['id']:
            self.logger.info(f'User {user_name} already assigned to team {team_name}')
            return
        if user['team_id'] is not None:
            self.remove_user_from_team(user_name)
        self.connector.post(f'/teams/{team["id"]}/members', json={'user_id': user["id"]})
        storage = self.storage.get_storage()
        storage['users'][user_name]['team_id'] = team['id']
        self.storage.save_storage(storage)

    async def aassign_user2team(self, user_name, team_name):
        user = self.storage.get_field_from_storage('users', user_name)
        team = self.storage.get_field_from_storage('teams', team_name)
        if user['team_id'] == team['id']:
            self.logger.info(f'User {user_name} already assigned to team {team_name}')
            return
        if user['team_id'] is not None:
            await self.aremove_user_from_team(user_name)
        await self.connector.apost(f'/teams/{team["id"]}/members', json={'user_id': user["id"]})
        storage = self.storage.get_storage()
        storage['users'][user_name]['team_id'] = team['id']
        self.storage.save_storage(storage)

    def _create_challenge_core(self, response, name):
        self.storage.update_storage_field_from_response(response, 'challenges', name)

    def _create_challenge_request(
        self,
        name: str,
        value: int,
        category: str = '',
        description: str = '',
        state='visible',
        challenge_type: str = 'standard',
    ):
        if self.storage.exist_in_field('challenges', name):
            self.logger.info(f'Challenge {name} already registered in CTFd')
            raise CTFdException(f'Challenge {name} already registered in CTFd')
        return ('/challenges',), {
            'json': {
                'category': category,
                'description': description,
                'state': state,
                'name': name,
                'type': challenge_type,
                'value': value,
            }
        }

    def create_challenge(
        self,
        name: str,
        value: int,
        category: str = '',
        description: str = '',
        state='visible',
        challenge_type: str = 'standard',
    ):
        args, kwargs = self._create_challenge_request(name, value, category, description, state, challenge_type)
        response = self.connector.post(*args, **kwargs)
        self._create_challenge_core(response, name)

    async def acreate_challenge(
        self,
        name: str,
        value: int,
        category: str = '',
        description: str = '',
        state='visible',
        challenge_type: str = 'standard',
    ):
        args, kwargs = self._create_challenge_request(name, value, category, description, state, challenge_type)
        response = await self.connector.apost(*args, **kwargs)
        self._create_challenge_core(response, name)

    def _create_flag_core(self, response, flag_name):
        self.storage.update_storage_field_from_response(response, 'flags', flag_name)

    def _create_flag_request(
        self, challenge_name: str, flag_name: str, flag: str, data: str = 'case_insensitive', flag_type='static'
    ):
        challenge = self.storage.get_field_from_storage('challenges', challenge_name)
        challenge_id = challenge['id']
        if self.storage.exist_in_field('flags', flag_name):
            self.logger.info(f'Flag {flag_name} already registered in CTFd')
            raise CTFdException(f'Flag {flag_name} already registered in CTFd')
        return ('/flags',), {'json': {'challenge_id': challenge_id, 'content': flag, 'data': data, 'type': flag_type}}

    def create_flag(
        self, challenge_name: str, flag_name: str, flag: str, data: str = 'case_insensitive', flag_type='static'
    ):
        args, kwargs = self._create_flag_request(challenge_name, flag_name, flag, data, flag_type)
        response = self.connector.post(*args, **kwargs)
        self._create_flag_core(response, flag_name)

    async def acreate_flag(
        self, challenge_name: str, flag_name: str, flag: str, data: str = 'case_insensitive', flag_type='static'
    ):
        args, kwargs = self._create_flag_request(challenge_name, flag_name, flag, data, flag_type)
        response = await self.connector.apost(*args, **kwargs)
        self._create_flag_core(response, flag_name)

    def _update_flag_request(self, name: str, flag: str, data: str = 'case_insensitive', flag_type='static'):
        field = self.storage.get_field_from_storage('flags', name)
        flag_id = field['id']
        return (f'/flags/{flag_id}',), {'json': {'content': flag, 'data': data, 'type': flag_type, 'id': flag_id}}

    def update_flag(self, name: str, flag: str, data: str = 'case_insensitive', flag_type='static'):
        args, kwargs = self._update_flag_request(name, flag, data, flag_type)
        self.connector.patch(*args, **kwargs)

    async def aupdate_flag(self, name: str, flag: str, data: str = 'case_insensitive', flag_type='static'):
        args, kwargs = self._update_flag_request(name, flag, data, flag_type)
        await self.connector.apatch(*args, **kwargs)

    def _delete_flag_request(self, name: str):
        field = self.storage.get_field_from_storage('flags', name)
        flag_id = field['id']
        return (f'/flags/{flag_id}',), {}

    def _delete_flag_core(self, name: str):
        self.storage.delete_storage_field('flags', name)

    def delete_flag(self, name: str):
        args, kwargs = self._delete_flag_request(name)
        self.connector.delete(*args, **kwargs)
        self._delete_flag_core(name)

    async def adelete_flag(self, name: str):
        args, kwargs = self._delete_flag_request(name)
        await self.connector.adelete(*args, **kwargs)
        self._delete_flag_core(name)

    async def adelete_user(self, name: str):
        user = self.storage.get_field_from_storage('users', name)
        user_id = user['id']
        await self.connector.adelete(f'/users/{user_id}')
        self.storage.delete_storage_field('users', name)

    def delete_user(self, name: str):
        user = self.storage.get_field_from_storage('users', name)
        user_id = user['id']
        self.connector.delete(f'/users/{user_id}')
        self.storage.delete_storage_field('users', name)

    async def adelete_team(self, name: str):
        team = self.storage.get_field_from_storage('teams', name)
        team_id = team['id']
        await self.connector.adelete(f'/teams/{team_id}')
        self.storage.delete_storage_field('teams', name)

    def delete_team(self, name: str):
        team = self.storage.get_field_from_storage('teams', name)
        team_id = team['id']
        self.connector.delete(f'/teams/{team_id}')
        self.storage.delete_storage_field('teams', name)

    async def adelete_challenge(self, name: str):
        challenge = self.storage.get_field_from_storage('challenges', name)
        challenge_id = challenge['id']
        await self.connector.adelete(f'/challenges/{challenge_id}')
        self.storage.delete_storage_field('challenges', name)

    def delete_challenge(self, name: str):
        challenge = self.storage.get_field_from_storage('challenges', name)
        challenge_id = challenge['id']
        self.connector.delete(f'/challenges/{challenge_id}')
        self.storage.delete_storage_field('challenges', name)

    async def aclear(self):
        storage = self.storage.get_storage()
        for user in storage['users']:
            await self.adelete_user(user)
        for team in storage['teams']:
            await self.adelete_team(team)
        for flag in storage['flags']:
            await self.adelete_flag(flag)
        for challenge in storage['challenges']:
            await self.adelete_challenge(challenge)

    def clear(self):
        storage = self.storage.get_storage()
        for user in storage['users']:
            self.delete_user(user)
        for team in storage['teams']:
            self.delete_team(team)
        for flag in storage['flags']:
            self.delete_flag(flag)
        for challenge in storage['challenges']:
            self.delete_challenge(challenge)


async def main():
    api = CtfdApi()
    # async
    # await api.acreate_user('user')
    # await api.acreate_team('team')
    # await api.aassign_user2team('user', 'team')
    # await api.acreate_challenge('challenge', 5)
    # await api.acreate_flag('challenge', 'flag', 'flag')
    # await api.aupdate_flag('flag', 'new_flag')
    # await api.adelete_flag('flag')
    # await api.adelete_challenge('challenge')
    # await api.adelete_user('user')
    # await api.adelete_team('team')
    # await api.aclear()

    # synchronous
    # api.create_user('sync_user')
    # api.create_team('sync_team')
    # api.assign_user2team('sync_user', 'sync_team')
    # api.create_challenge('sync_challenge', 5)
    # api.create_flag('sync_challenge', 'sync_flag', 'flag')
    # api.update_flag('sync_flag', 'new_flag')
    # api.delete_flag('sync_flag')
    # api.delete_challenge('sync_challenge')
    # api.delete_user('sync_user')
    # api.delete_team('sync_team')
    # api.clear()


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
