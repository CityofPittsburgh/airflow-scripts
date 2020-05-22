""" from __future__ import absolute_import

import os

from airflow import DAG, configuration, models
from airflow.operators.bash_operator import BashOperator
from airflow.operators.docker_operator import DockerOperator
from airflow.contrib.operators.dataflow_operator import DataFlowPythonOperator
from airflow.contrib.operators.gcs_to_bq import GoogleCloudStorageToBigQueryOperator
from airflow.contrib.operators.bigquery_operator import BigQueryOperator
from airflow.operators.python_operator import PythonOperator
from datetime import datetime, timedelta
from dependencies import airflow_utils
from dependencies.airflow_utils import yesterday, dt, build_revgeo_query, filter_old_values

# TODO: When Airflow 2.0 is released, upgrade the package, upgrade the virtualenv to Python3,
# and add the arg py_interpreter='python3' to DataFlowPythonOperator

# We set the start_date of the DAG to the previous date, as defined in airflow_utils. This will
# make the DAG immediately available for scheduling.

default_args = {
    'depends_on_past': False,
    'start_date': yesterday,
    'email': os.environ['EMAIL'],
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
    'project_id': os.environ['GCP_PROJECT'],
    'dataflow_default_options': {
        'project': os.environ['GCP_PROJECT']
    }
}

dag = DAG(
    'otrs', default_args=default_args, schedule_interval=timedelta(days=1))

otrs_to_gcs = BashOperator(
    task_id='otrs_to_gcs',
    bash_command='python {}'.format(os.getcwd() + '/airflow_scripts/dags/dependencies/gcs_loaders'
                                                  '/otrs_gcs.py'),
    dag=dag
)

gcs_to_csv = DockerOperator(
    task_id='gcs_to_csv',
    image='gcr.io/data-rivers/pgh-otrs',
    api_version='auto',
    auto_remove=True,
    environment={
        'OTRS_IP': os.environ['OTRS_IP'],
        'OTRS_USER': os.environ['OTRS_USER'],
        'OTRS_PW': os.environ['OTRS_PW'],
        'GCS_AUTH_FILE': '/root/otrs_gcs/data_rivers_key.json',
        'GCS_PREFIX': os.environ['GCS_PREFIX']
    },
    dag=dag
)

## two dataflow for tickets and surveys (similar to finance)

otrs_tickets_dataflow = BashOperator(
    task_id='otrs_tickets_dataflow',
    bash_command='python {}'.format(os.getcwd() + '/airflow_scripts/dags/dependencies/dataflow_scripts'
                                                  '/otrs_tickets_dataflow.py'),
    dag=dag
)

otrs_surveys_dataflow = BashOperator(
    task_id='otrs_surveys_dataflow',
    bash_command='python {}'.format(os.getcwd() + '/airflow_scripts/dags/dependencies/dataflow_scripts'
                                                  '/otrs_surveys_dataflow.py'),
    dag=dag
)

## two avro files pushing to bigquery

otrs_tickets_bq = GoogleCloudStorageToBigQueryOperator(
    task_id='otrs_tickets_bq',
    destination_project_dataset_table='{}:otrs.tickets'.format(os.environ['GCP_PROJECT']),
    bucket='{}_otrs'.format(os.environ['GCS_PREFIX']),
    source_objects=["tickets/avro_output/{}/{}/{}/*.avro".format(dt.strftime('%Y'),
                                                                    dt.strftime('%m').lower(),
                                                                    dt.strftime("%Y-%m-%d"))],
    write_disposition='WRITE_TRUNCATE',
    create_disposition='CREATE_IF_NEEDED',
    source_format='AVRO',
    time_partitioning={'type': 'DAY'},
    dag=dag
)

otrs_surveys_bq = GoogleCloudStorageToBigQueryOperator(
    task_id='otrs_surveys_bq',
    destination_project_dataset_table='{}:otrs.surveys'.format(os.environ['GCP_PROJECT']),
    bucket='{}_otrs'.format(os.environ['GCS_PREFIX']),
    source_objects=["surveys/avro_output/{}/{}/{}/*.avro".format(dt.strftime('%Y'),
                                                                  dt.strftime('%m').lower(),
                                                                  dt.strftime("%Y-%m-%d"))],
    write_disposition='WRITE_TRUNCATE',
    create_disposition='CREATE_IF_NEEDED',
    source_format='AVRO',
    time_partitioning={'type': 'DAY'},
    dag=dag
)


otrs_beam_cleanup = BashOperator(
    task_id='otrs_beam_cleanup',
    bash_command=airflow_utils.beam_cleanup_statement('{}_otrs'.format(os.environ['GCS_PREFIX'])),
    dag=dag
)

otrs_to_gcs >> gcs_to_csv >> otrs_tickets_dataflow >> (otrs_tickets_bq, otrs_beam_cleanup)

otrs_to_gcs >> gcs_to_csv >> otrs_surveys_dataflow >> (otrs_surveys_bq, otrs_beam_cleanup) """