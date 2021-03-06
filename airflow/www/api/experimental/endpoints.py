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
from airflow.exceptions import AirflowException
from airflow.www.app import csrf
from airflow import models

from flask import (
    g, Markup, Blueprint, redirect, jsonify, abort, request, current_app, send_file
)
from datetime import datetime

_log = logging.getLogger(__name__)

requires_authentication = airflow.api.api_auth.requires_authentication

api_experimental = Blueprint('api_experimental', __name__)

_log = logging.getLogger(__name__)


@csrf.exempt
@api_experimental.route('/dags/<string:dag_id>/dag_runs', methods=['POST'])
@requires_authentication
def trigger_dag(dag_id):
    """
    Trigger a new dag run for a Dag with an execution date of now
    """
    data = request.get_json(force=True)

    run_id = None
    if 'run_id' in data:
        run_id = data['run_id']

    conf = None
    if 'conf' in data:
        conf = data['conf']

    try:
        dr = trigger.trigger_dag(dag_id, run_id, conf)
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
@api_experimental.route('/dags/<string:dag_id>/dag_runs/<string:execution_date>', methods=['POST'])
@requires_authentication
def trigger_dag_for_date(dag_id, execution_date):
    """
    Trigger a new dag run for a Dag with the given execution date. The
    format for the execution date is expected to be "YYYY-mm-DDTHH:MM:SS",
    for example: "2016-11-16T11:34:15". The colons ought to be escaped to %3A,
    as you would expect, within the URL. These are then automatically replaced
    by Flask before being passed into this method.
    """
    data = request.get_json(force=True)

    run_id = None
    if 'run_id' in data:
        run_id = data['run_id']

    conf = None
    if 'conf' in data:
        conf = data['conf']

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
        logging.error(err)
        response = jsonify(error="{}".format(err))
        response.status_code = 404
        return response

    if getattr(g, 'user', None):
        _log.info("User {} created {}".format(g.user, dr))

    response = jsonify(message="Created {}".format(dr))
    return response


@api_experimental.route('/test', methods=['GET'])
@requires_authentication
def test():
    return jsonify(status='OK')


@api_experimental.route('/dags/<string:dag_id>/tasks/<string:task_id>', methods=['GET'])
@requires_authentication
def task_info(dag_id, task_id):
    """Returns a JSON with a task's public instance variables. """
    from airflow.www.views import dagbag

    if dag_id not in dagbag.dags:
        response = jsonify(error='Dag {} not found'.format(dag_id))
        response.status_code = 404
        return response

    dag = dagbag.dags[dag_id]
    if not dag.has_task(task_id):
        response = (jsonify(error='Task {} not found in dag {}'
                    .format(task_id, dag_id)))
        response.status_code = 404
        return response

    task = dag.get_task(task_id)
    fields = {k: str(v) for k, v in vars(task).items() if not k.startswith('_')}
    return jsonify(fields)


@api_experimental.route('/dags/<string:dag_id>/tasks/<string:task_id>/instances/<string:execution_date>', methods=['GET'])
@requires_authentication
def task_instance_info(dag_id, task_id, execution_date):
    """
    Returns a JSON with a task instance's public instance variables. The
    format for the execution date is expected to be "YYYY-mm-DDTHH:MM:SS",
    for example: "2016-11-16T11:34:15". The colons ought to be escaped to %3A,
    as you would expect, within the URL. These are then automatically replaced
    by Flask before being passed into this method.
    """
    from airflow.www.views import dagbag

    _log.info('TaskState API called with parameters: dag_id: {}; '
              'task_id: {}; execution_date: {}'.format(dag_id,
                                                       task_id,
                                                       execution_date))

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

    # Get DAG object and check task exists
    dag = dagbag.dags[dag_id]
    if not dag.has_task(task_id):
        error_message = 'Task {} not found in dag {}'.format(task_id, dag_id)
        response = jsonify(error=error_message)
        response.status_code = 404
        return response

    # Get DagRun object and check that it exists
    dagrun = dag.get_dagrun(execution_date=execution_date)
    if not dagrun:
        error_message = ('Dag Run for date {} not found in dag {}'
                         .format(execution_date, dag_id))
        response = jsonify(error=error_message)
        response.status_code = 404
        return response

    # Get task instance object and check that it exists
    task_instance = dagrun.get_task_instance(task_id)
    if not task_instance:
        error_message = ('Task {} instance for date {} not found'
                         .format(task_id, execution_date))
        response = jsonify(error=error_message)
        response.status_code = 404
        return response

    # JSONify and return.
    fields = {k: str(v)
              for k, v in vars(task_instance).items()
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
