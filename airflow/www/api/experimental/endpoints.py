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

import airflow.api

from airflow.api.common.experimental import trigger_dag as trigger
from airflow.api.common.experimental.get_dag import get_dag
from airflow.api.common.experimental.get_dag_run import get_dag_run
from airflow.api.common.experimental.get_task import get_task
from airflow.api.common.experimental.get_task_instance import get_task_instance
from airflow.exceptions import AirflowException
from airflow.www.app import csrf
from airflow import models

from flask import (
    g, Markup, Blueprint, redirect, jsonify, abort,
    request, current_app, send_file, url_for
)
from datetime import datetime

_log = logging.getLogger(__name__)

requires_authentication = airflow.api.api_auth.requires_authentication

api_experimental = Blueprint('api_experimental', __name__)


@csrf.exempt
@api_experimental.route('/dags/<string:dag_id>/dag_runs', methods=['POST'])
@requires_authentication
def trigger_dag(dag_id):
    """
    Trigger a new dag run for a Dag with an execution date of now unless
    specified in the data.
    """
    data = request.get_json(force=True)

    run_id = None
    if 'run_id' in data:
        run_id = data['run_id']

    conf = None
    if 'conf' in data:
        conf = data['conf']

    execution_date = None
    if 'execution_date' in data and data['execution_date'] is not None:
        execution_date = data['execution_date']

        # Convert string datetime into actual datetime
        try:
            execution_date = datetime.strptime(execution_date,
                                               '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            error_message = (
                'Given execution date, {}, could not be identified '
                'as a date. Example date format: 2015-11-16T14:34:15'
                .format(execution_date))
            _log.info(error_message)
            response = jsonify({'error': error_message})
            response.status_code = 400

            return response

    try:
        dr = trigger.trigger_dag(dag_id, run_id, conf, execution_date)
    except AirflowException as err:
        _log.error(err)
        response = jsonify(error="{}".format(err))
        response.status_code = 404
        return response

    if getattr(g, 'user', None):
        _log.info("User {} created {}".format(g.user, dr))

    response = jsonify(message="Created {}".format(dr))
    return response


@csrf.exempt
@api_experimental.route('/dags/<string:dag_id>', methods=['GET'])
@requires_authentication
def dag_info(dag_id):
    """
    Returns a JSON with a DAG's public instance variables as well
    as how many dag runs are in progress.
    """

    try:
        dag = get_dag(dag_id)
        active_run_dates = dag.get_active_runs()
        info = {k: str(v)
                for k, v in vars(dag).items()
                if not k.startswith('_')}
        info['active_runs'] = [date.isoformat() for date in active_run_dates]
        return jsonify(info)
    except AirflowException as err:
        _log.info(err)
        response = jsonify(error="{}".format(err))
        response.status_code = 404
        return response


@csrf.exempt
@api_experimental.route('/dags/<string:dag_id>/dag_runs/<string:execution_date>', methods=['GET'])
@requires_authentication
def dag_run_info(dag_id, execution_date):
    """
    Returns a JSON with a DAG Run's public instance variables.
    """

        # Convert string datetime into actual datetime
        try:
            execution_date = datetime.strptime(execution_date,
                                               '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            error_message = (
                'Given execution date, {}, could not be identified '
                'as a date. Example date format: 2015-11-16T14:34:15'
                .format(execution_date))
            _log.info(error_message)
            response = jsonify({'error': error_message})
            response.status_code = 400

            return response

    try:
        dag_run = get_dag_run(dag_id, execution_date)
        info = {k: str(v)
                for k, v in vars(dag_run).items()
                if not k.startswith('_')}
        task_instances = dag_run.get_task_instances()
        info['task_instances'] = [{k: str(v)
                                   for k, v in vars(instance).items()
                                   if not k.startswith('_')} for instance in task_instances]
        return jsonify(info)
    except AirflowException as err:
        _log.info(err)
        response = jsonify(error="{}".format(err))
        response.status_code = 404
        return response


@api_experimental.route('/test', methods=['GET'])
@requires_authentication
def test():
    return jsonify(status='OK')


@api_experimental.route('/dags/<string:dag_id>/tasks/<string:task_id>', methods=['GET'])
@requires_authentication
def task_info(dag_id, task_id):
    """Returns a JSON with a task's public instance variables. """

    try:
        info = get_task(dag_id, task_id)
    except AirflowException as err:
        _log.info(err)
        response = jsonify(error="{}".format(err))
        response.status_code = 404
        return response

    # JSONify and return.
    fields = {k: str(v)
              for k, v in vars(info).items()
              if not k.startswith('_')}
    return jsonify(fields)


@api_experimental.route('/latest_runs', methods=['GET'])
@requires_authentication
def latest_dag_runs():
    """Returns the latest running DagRun for each DAG formatted for the UI. """
    from airflow.models import DagRun
    dagruns = DagRun.get_latest_runs()
    payload = []
    for dagrun in dagruns:
        if dagrun.execution_date:
            payload.append({
                'dag_id': dagrun.dag_id,
                'execution_date': dagrun.execution_date.strftime("%Y-%m-%d %H:%M"),
                'start_date': ((dagrun.start_date or '') and
                               dagrun.start_date.strftime("%Y-%m-%d %H:%M")),
                'dag_run_url': url_for('airflow.graph', dag_id=dagrun.dag_id,
                                       execution_date=dagrun.execution_date)
            })
    return jsonify(payload)
    
@api_experimental.route('/dags/<string:dag_id>/dag_runs/<string:execution_date>/tasks/<string:task_id>', methods=['GET'])
@requires_authentication
def task_instance_info(dag_id, execution_date, task_id):
    """
    Returns a JSON with a task instance's public instance variables.
    The format for the exec_date is expected to be
    "YYYY-mm-DDTHH:MM:SS", for example: "2016-11-16T11:34:15". This will
    of course need to have been encoded for URL in the request.
    """

    # Convert string datetime into actual datetime
    try:
        execution_date = datetime.strptime(execution_date,
                                           '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        error_message = (
            'Given execution date, {}, could not be identified '
            'as a date. Example date format: 2015-11-16T14:34:15'
            .format(execution_date))
        _log.info(error_message)
        response = jsonify({'error': error_message})
        response.status_code = 400

        return response

    try:
        info = get_task_instance(dag_id, task_id, execution_date)
    except AirflowException as err:
        _log.info(err)
        response = jsonify(error="{}".format(err))
        response.status_code = 404
        return response

    # JSONify and return.
    fields = {k: str(v)
              for k, v in vars(info).items()
              if not k.startswith('_')}
    return jsonify(fields)


@csrf.exempt
@api_experimental.route('/dags/<string:dag_id>/tasks/<string:task_id>/instances/<string:execution_date>/xcom/<string:key>/<string:value>', methods=['POST'])
@requires_authentication
def write_xcom(dag_id, task_id, execution_date, key, value):
    """
    Writes the given key value pair to the xcom table with the properties
    given. This will update the entry if it already exists, otherwise it will
    create a new entry. The format for the execution date is expected to be
    "YYYY-mm-DDTHH:MM:SS", for example: "2016-11-16T11:34:15". The colons ought
    to be escaped to %3A, as you would expect, within the URL. These are then
    automatically replaced by Flask before being passed into this method.
    """
    from airflow.www.views import dagbag

    _log.info('WriteXCom API called with parameters: dag_id: {}; task_id: {}; '
              'execution_date: {}; key: {}; value: {}'.format(dag_id,
                                                              task_id,
                                                              execution_date,
                                                              key,
                                                              value))

    # Convert string datetime into actual datetime
    try:
        execution_date = datetime.strptime(execution_date, '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        error_message = (
            'Given execution date, {}, could not be identified '
            'as a date. Example date format: 2015-11-16T14:34:15'
            .format(execution_date))
        _log.info(error_message)
        response = jsonify({'error': error_message})
        response.status_code = 400

        return response

    # Check DAG exists
    if dag_id not in dagbag.dags:
        error_message = 'Dag {} not found'.format(dag_id)
        response = jsonify(error=error_message)
        response.status_code = 404
        return response

    # Set the XCom object. Duplicate objects are handled and overwritten inside
    # this method.
    models.XCom.set(
        dag_id=dag_id,
        task_id=task_id,
        execution_date=execution_date,
        key=key,
        value=value)

    response = jsonify(message="XCom {} has been set to {} for task {} "
                               "in DAG {} with the execution date {}"
                               .format(key,
                                       value,
                                       task_id,
                                       dag_id,
                                       execution_date))

    return response
