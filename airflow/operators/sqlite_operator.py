# -*- coding: utf-8 -*-
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

from airflow.hooks.sqlite_hook import SqliteHook
from airflow.models import BaseOperator
from airflow.utils.decorators import apply_defaults

_log = logging.getLogger(__name__)


class SqliteOperator(BaseOperator):
    """
    Executes sql code in a specific Sqlite database

    :param sqlite_conn_id: reference to a specific sqlite database
    :type sqlite_conn_id: string
    :param sql: the sql code to be executed
    :type sql: string or string pointing to a template file. File must have
        a '.sql' extensions.
    """

    template_fields = ('sql',)
    template_ext = ('.sql',)
    ui_color = '#cdaaed'

    @apply_defaults
    def __init__(
            self, sql, sqlite_conn_id='sqlite_default', parameters=None,
            *args, **kwargs):
        super(SqliteOperator, self).__init__(*args, **kwargs)
        self.sqlite_conn_id = sqlite_conn_id
        self.sql = sql
        self.parameters = parameters or []

    def execute(self, context):
        _log.info('Executing: ' + self.sql)
        hook = SqliteHook(sqlite_conn_id=self.sqlite_conn_id)
        hook.run(self.sql, parameters=self.parameters)
