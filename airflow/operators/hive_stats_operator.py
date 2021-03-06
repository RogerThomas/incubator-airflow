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

from builtins import str
from builtins import zip
from collections import OrderedDict
import json
import logging

from airflow.exceptions import AirflowException
from airflow.hooks.mysql_hook import MySqlHook
from airflow.hooks.presto_hook import PrestoHook
from airflow.hooks.hive_hooks import HiveMetastoreHook
from airflow.models import BaseOperator
from airflow.utils.decorators import apply_defaults

_log = logging.getLogger(__name__)


class HiveStatsCollectionOperator(BaseOperator):
    """
    Gathers partition statistics using a dynamically generated Presto
    query, inserts the stats into a MySql table with this format. Stats
    overwrite themselves if you rerun the same date/partition.

    ``
    CREATE TABLE hive_stats (
        ds VARCHAR(16),
        table_name VARCHAR(500),
        metric VARCHAR(200),
        value BIGINT
    );
    ``

    :param table: the source table, in the format ``database.table_name``
    :type table: str
    :param partition: the source partition
    :type partition: dict of {col:value}
    :param extra_exprs: dict of expression to run against the table where
        keys are metric names and values are Presto compatible expressions
    :type extra_exprs: dict
    :param col_blacklist: list of columns to blacklist, consider
        blacklisting blobs, large json columns, ...
    :type col_blacklist: list
    :param assignment_func: a function that receives a column name and
        a type, and returns a dict of metric names and an Presto expressions.
        If None is returned, the global defaults are applied. If an
        empty dictionary is returned, no stats are computed for that
        column.
    :type assignment_func: function
    """

    template_fields = ('table', 'partition', 'ds', 'dttm')
    ui_color = '#aff7a6'

    @apply_defaults
    def __init__(
            self,
            table,
            partition,
            extra_exprs=None,
            col_blacklist=None,
            assignment_func=None,
            metastore_conn_id='metastore_default',
            presto_conn_id='presto_default',
            mysql_conn_id='airflow_db',
            *args, **kwargs):
        super(HiveStatsCollectionOperator, self).__init__(*args, **kwargs)

        self.table = table
        self.partition = partition
        self.extra_exprs = extra_exprs or {}
        self.col_blacklist = col_blacklist or {}
        self.metastore_conn_id = metastore_conn_id
        self.presto_conn_id = presto_conn_id
        self.mysql_conn_id = mysql_conn_id
        self.assignment_func = assignment_func
        self.ds = '{{ ds }}'
        self.dttm = '{{ execution_date.isoformat() }}'

    def get_default_exprs(self, col, col_type):
        if col in self.col_blacklist:
            return {}
        d = {}
        d[(col, 'non_null')] = "COUNT({col})"
        if col_type in ['double', 'int', 'bigint', 'float', 'double']:
            d[(col, 'sum')] = 'SUM({col})'
            d[(col, 'min')] = 'MIN({col})'
            d[(col, 'max')] = 'MAX({col})'
            d[(col, 'avg')] = 'AVG({col})'
        elif col_type == 'boolean':
            d[(col, 'true')] = 'SUM(CASE WHEN {col} THEN 1 ELSE 0 END)'
            d[(col, 'false')] = 'SUM(CASE WHEN NOT {col} THEN 1 ELSE 0 END)'
        elif col_type in ['string']:
            d[(col, 'len')] = 'SUM(CAST(LENGTH({col}) AS BIGINT))'
            d[(col, 'approx_distinct')] = 'APPROX_DISTINCT({col})'

        return {k: v.format(col=col) for k, v in d.items()}

    def execute(self, context=None):
        metastore = HiveMetastoreHook(metastore_conn_id=self.metastore_conn_id)
        table = metastore.get_table(table_name=self.table)
        field_types = {col.name: col.type for col in table.sd.cols}

        exprs = {
            ('', 'count'): 'COUNT(*)'
        }
        for col, col_type in list(field_types.items()):
            d = {}
            if self.assignment_func:
                d = self.assignment_func(col, col_type)
                if d is None:
                    d = self.get_default_exprs(col, col_type)
            else:
                d = self.get_default_exprs(col, col_type)
            exprs.update(d)
        exprs.update(self.extra_exprs)
        exprs = OrderedDict(exprs)
        exprs_str = ",\n        ".join([
            v + " AS " + k[0] + '__' + k[1]
            for k, v in exprs.items()])

        where_clause = [
            "{0} = '{1}'".format(k, v) for k, v in self.partition.items()]
        where_clause = " AND\n        ".join(where_clause)
        sql = """
        SELECT
            {exprs_str}
        FROM {self.table}
        WHERE
            {where_clause};
        """.format(**locals())

        hook = PrestoHook(presto_conn_id=self.presto_conn_id)
        _log.info('Executing SQL check: ' + sql)
        row = hook.get_first(hql=sql)
        _log.info("Record: " + str(row))
        if not row:
            raise AirflowException("The query returned None")

        part_json = json.dumps(self.partition, sort_keys=True)

        _log.info("Deleting rows from previous runs if they exist")
        mysql = MySqlHook(self.mysql_conn_id)
        sql = """
        SELECT 1 FROM hive_stats
        WHERE
            table_name='{self.table}' AND
            partition_repr='{part_json}' AND
            dttm='{self.dttm}'
        LIMIT 1;
        """.format(**locals())
        if mysql.get_records(sql):
            sql = """
            DELETE FROM hive_stats
            WHERE
                table_name='{self.table}' AND
                partition_repr='{part_json}' AND
                dttm='{self.dttm}';
            """.format(**locals())
            mysql.run(sql)

        _log.info("Pivoting and loading cells into the Airflow db")
        rows = [
            (self.ds, self.dttm, self.table, part_json) +
            (r[0][0], r[0][1], r[1])
            for r in zip(exprs, row)]
        mysql.insert_rows(
            table='hive_stats',
            rows=rows,
            target_fields=[
                'ds',
                'dttm',
                'table_name',
                'partition_repr',
                'col',
                'metric',
                'value',
            ]
        )
