"""Lazy database engines behind the single WHU MCP entry point."""
from __future__ import annotations

import importlib

from mcp.server.fastmcp.utilities.func_metadata import func_metadata
from pydantic import ValidationError

ENGINES = {'cnki': 'academic_data_mcp.connectors.cnki',
           'csmar': 'academic_data_mcp.connectors.csmar'}
ALIASES = {'知网': 'cnki', '中国知网': 'cnki', '国泰安': 'csmar'}


class DatabaseAdapters:
    def __init__(self, config, session):
        self.config, self.session = config, session
        self.runtimes = {}

    def identify(self, database):
        name = ALIASES.get(database.strip(), database.strip().lower())
        if name not in ENGINES:
            raise ValueError('该数据库暂未提供库内操作适配器；请使用 whu_open_database / whu_read_page。可用适配器：cnki、csmar。')
        return name

    def operations(self, database):
        name = self.identify(database)
        if name not in self.runtimes:
            self.runtimes[name] = importlib.import_module(ENGINES[name]).create(self.config)
        return name, {t.name.removeprefix(name + '_'): t for t in self.runtimes[name].tools()}

    @staticmethod
    def instructions(value, name, operations):
        if isinstance(value, str):
            for op in operations:
                value = value.replace(name + '_' + op,
                    f"whu_call_database(database='{name}', operation='{op}')")
        return value

    async def capabilities(self, database: str):
        async def op():
            name, operations = self.operations(database)
            return {'success': True, 'database': name, 'entry_tool': 'whu_call_database',
                    'operations': [{'operation': key,
                                    'description': self.instructions(spec.description, name, operations),
                                    'parameters': func_metadata(spec.handler).arg_model.model_json_schema(),
                                    'consumes_quota': name == 'csmar' and key == 'download'}
                                   for key, spec in operations.items()],
                    'note': '这是库内操作能力列表，不代表已登录或有全部子库权限。'}
        return await self.session.run(op)

    async def call(self, database: str, operation: str, arguments: dict | None = None):
        async def op():
            name, operations = self.operations(database)
            if operation not in operations:
                raise ValueError('未知库内操作；请先调用 whu_database_capabilities。')
            spec = operations[operation]
            try:
                model = func_metadata(spec.handler).arg_model
                if set(arguments or {}) - set(model.model_fields):
                    raise ValueError('库内操作包含未知参数。')
                values = model.model_validate(arguments or {}).model_dump()
            except ValidationError as error:
                raise ValueError('库内操作参数不匹配：' + '; '.join(
                    '.'.join(map(str, e['loc'])) + ': ' + e['msg'] for e in error.errors())) from None
            result = await spec.handler(**values)
            if isinstance(result, dict):
                result = {**result, 'database': name, 'operation': operation}
                result.setdefault('success', True)
                for key in ('message', 'detail', 'next_step'):
                    if key in result:
                        result[key] = self.instructions(result[key], name, operations)
                if result.get('login_required'):
                    result['error_code'] = 'authentication_required'
                if operation == 'open_login_page' and result.get('success'):
                    result['next_call'] = {'tool': 'whu_call_database', 'database': name,
                                           'operation': 'finish_login'}
            return result
        return await self.session.run(op, download=operation in ('download', 'download_pdf'))

    async def aclose(self):
        for runtime in reversed(list(self.runtimes.values())):
            await runtime.aclose()
        self.runtimes.clear()
