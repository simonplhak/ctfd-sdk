import json
import logging
from pathlib import Path

import httpx

from ctfd_sdk import settings


class CTFdException(Exception):
    pass


class CtfdConnector:
    def __init__(self, admin_token: str, host: str):
        self.admin_token = admin_token
        self.host = host.strip('/')
        self.logger = logging.getLogger('ctfd')

    def assign_host(func):
        async def inner(self, *args, **kwargs):
            url = f'{self.host}/api/v1{args[0]}'
            if 'headers' not in kwargs:
                kwargs['headers'] = {}
            kwargs['headers']['Content-Type'] = 'application/json'
            kwargs['headers']['Authorization'] = f'Token {self.admin_token}'
            response = await func(self, url, *list(args)[1:], **kwargs)  # noqa
            if response.status_code not in [200, 201]:
                msg = f'Unable to get response in method {func.__name__} from url {url}  in CTFd: {response.text}'
                self.logger.warning(msg)
                raise CTFdException(msg)
            return response

        return inner

    @assign_host
    async def get(self, url, *args, **kwargs):
        async with httpx.AsyncClient() as client:
            response = await client.get(url, *args, **kwargs)
        return response

    @assign_host
    async def post(self, url, *args, **kwargs):
        async with httpx.AsyncClient() as client:
            response = await client.post(url, *args, **kwargs)
            # response.status_code = 1
        return response

    @assign_host
    async def patch(self, url, *args, **kwargs):
        async with httpx.AsyncClient() as client:
            response = await client.patch(url, *args, **kwargs)
        return response

    @assign_host
    async def delete(self, url, *args, **kwargs):
        async with httpx.AsyncClient() as client:
            response = await client.delete(url, *args, **kwargs)
        return response


class CtfdApi:
    def __init__(self, admin_token: str = None, host: str = None, storage_path: str | Path = None):
        admin_token = admin_token or settings.CTFD_ADMIN_TOKEN
        assert admin_token is not None, 'To use ctfd you need to define "CTFD_ADMIN_TOKEN"'
        host = host or settings.CTFD_HOST
        assert host is not None, 'To use ctfd you need to define "CTFD_HOST"'
        self.connector = CtfdConnector(admin_token, host)
        self.storage_path = storage_path or settings.CTFD_STORAGE
        assert self.storage_path is not None, 'To use ctfd you need to define "CTFD_STORAGE"'
        self.storage_path = Path(self.storage_path)
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

    async def create_user(self, username: str, is_admin=False):
        response = await self.connector.post(
            '/users',
            json={
                'banned': False,
                'email': f'{username}@email.com',
                'fields': [],
                'hidden': False,
                'name': username,
                'password': username,
                'type': 'admin' if is_admin else 'user',
                'verified': True,
            },
        )
        data = response.json()['data']
        storage = self.get_storage()
        storage['users'][username] = {'id': data['id'], 'team_id': None}
        self.save_storage(storage)
        self.logger.info(f'Agent with username: {username} registered in CTFd')

    async def create_team(self, name: str):
        response = await self.connector.post(
            '/teams',
            json={
                'banned': False,
                'country': 'CZ',
                'email': f'{name}@copas.cz',
                'fields': [],
                'hidden': False,
                'name': name,
                'password': name,
            },
        )
        self.update_storage_field_from_response(response, 'teams', name)

    async def remove_user_from_team(self, user_name):
        user = self.get_field_from_storage('users', user_name)
        if user['team_id'] is None:
            self.logger.info(f'User {user_name} not assigned to any team')
            return

    async def assign_user2team(self, user_name, team_name):
        user = self.get_field_from_storage('users', user_name)
        team = self.get_field_from_storage('teams', team_name)
        if user['team_id'] == team['id']:
            self.logger.info(f'User {user_name} already assigned to team {team_name}')
            return
        if user['team_id'] is not None:
            await self.remove_user_from_team(user_name)
        await self.connector.post(f'/teams/{team["id"]}/members', json={'user_id': user["id"]})
        storage = self.get_storage()
        storage['users'][user_name]['team_id'] = team['id']
        self.save_storage(storage)

    async def create_challenge(
        self,
        name: str,
        value: int,
        category: str = '',
        description: str = '',
        state='visible',
        challenge_type: str = 'standard',
    ):
        response = await self.connector.post(
            '/challenges',
            json={
                'category': category,
                'description': description,
                'state': state,
                'name': name,
                'type': challenge_type,
                'value': value,
            },
        )
        self.update_storage_field_from_response(response, 'challenges', name)

    async def create_flag(
        self, challenge_name: str, flag_name: str, flag: str, data: str = 'case_insensitive', flag_type='static'
    ):
        challenge = self.get_field_from_storage('challenges', challenge_name)
        challenge_id = challenge['id']
        response = await self.connector.post(
            '/flags',
            json={'challenge_id': challenge_id, 'content': flag, 'data': data, 'type': flag_type},
        )
        self.update_storage_field_from_response(response, 'flags', flag_name)

    async def update_flag(self, name: str, flag: str, data: str = 'case_insensitive', flag_type='static'):
        field = self.get_field_from_storage('flags', name)
        flag_id = field['id']
        await self.connector.patch(
            f'/flags/{flag_id}',
            json={'content': flag, 'data': data, 'type': flag_type, 'id': flag_id},
        )

    async def delete_flag(self, name: str):
        field = self.get_field_from_storage('flags', name)
        flag_id = field['id']
        await self.connector.delete(f'/flags/{flag_id}')


async def main():
    api = CtfdApi(host='http://localhost:8005')
    # await api.create_user('user')
    # await api.create_team('team')
    # await api.assign_user2team('user', 'team')
    # await api.create_challenge('challenge', 5)
    # await api.create_flag('challenge', 'flag', 'flag')
    # await api.update_flag('flag', 'new_flag')
    await api.delete_flag('flag')


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
