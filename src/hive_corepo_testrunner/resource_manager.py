#!/usr/bin/env python
# -*- coding: utf-8 -*-
# -*- mode: python -*-
"""
:mod:`acceptance_tester.testsuite_runners.hive_corepo.resource_manager` -- Resource manager for hive-corepo
===========================================================================================================

============================
Hive-corepo resource manager
============================

Resource Manager for hive-service/corepo-repository integration
testing.
"""
import logging
import hashlib
import os
import subprocess
import time

from os_python.common.net.iserver import IServer
from os_python.common.utils.init_functions import die
from acceptance_tester.abstract_testsuite_runner.resource_manager import AbstractResourceManager
from os_python.common.utils.init_functions import NullHandler
from os_python.docker.docker_container import DockerContainer
from os_python.docker.docker_container import ContainerSuitePool
from os_python.connectors.postgres import PostgresDockerConnector

from os_python.wiremock_helper import wiremock_load_rules_from_dir

from configobj import ConfigObj

### define logger
logger = logging.getLogger( "dbc."+__name__ )
logger.addHandler( NullHandler() )

class ContainerPoolImpl(ContainerSuitePool):

    def __init__(self, resource_folder):
        super(ContainerPoolImpl, self).__init__()
        self.resource_folder = resource_folder

    def create_suite(self, suite):
        suite_name = "_hive_corepo_%f" % time.time()

        corepo_db = suite.create_container("corepo-db", image_name=DockerContainer.secure_docker_image('corepo-postgresql-1.2'),
                             name="corepo-db" + suite_name,
                             environment_variables={"POSTGRES_USER": "corepo",
                                                    "POSTGRES_PASSWORD": "corepo",
                                                    "POSTGRES_DB": "corepo"},
                             start_timeout=1200)
        wiremock = suite.create_container("wiremock", image_name=DockerContainer.secure_docker_image('os-wiremock-1.0-snapshot'),
                             name="wiremock" + suite_name,
                             start_timeout=1200)
        wiremock.start()
        corepo_db.start()
        corepo_db.waitFor("database system is ready to accept connections")
        wiremock.waitFor("verbose:")

        corepo_db_root = "corepo:corepo@%s:5432/corepo" % corepo_db.get_ip()

        wiremock_load_rules_from_dir("http://%s:8080" % wiremock.get_ip(), self.resource_folder)

        corepo_content_service = suite.create_container("corepo-content-service",
                                                        image_name=DockerContainer.secure_docker_image('corepo-content-service-1.2'),
                                                        name="corepo-content-service" + suite_name,
                                                        environment_variables={"COREPO_POSTGRES_URL": corepo_db_root,
                                                                               "OPEN_AGENCY_URL": "http://%s:8080" % wiremock.get_ip(),
                                                                               "LOG__dk_dbc": "TRACE",
                                                                               "JAVA_MAX_HEAP_SIZE": "2G",
                                                                               "READONLY": "False",
                                                                               "PAYARA_STARTUP_TIMEOUT": 1200},
                                                        start_timeout=1200)


        hive = suite.create_container("hive",
                                      image_name=DockerContainer.secure_docker_image('hive-app-1.0-snapshot'),
                                      name="hive" + suite_name,
                                      environment_variables={"REPOSITORY_URL": "jdbc:postgresql://corepo:corepo@%s:5432/corepo" % corepo_db.get_ip(),
                                                             "HARVEST_MODE": "SERVER",
                                                             "HARVEST_HARVESTER": "ESFileRecordFeeder",
                                                             "HOLDINGSDB_URL": "",
                                                             "ADDISERVICE_URL": "",
                                                             "BATCHEXCHANGE_JDBCURL": "",
                                                             "OPENAGENCY_URL": "http://%s:8080" % wiremock.get_ip(),
                                                             "HIVE_POOLSIZE": 1,
                                                             "HARVEST_POLLINTERVAL":2,
                                                             "LOG__dk_dbc": "TRACE"},
                                      start_timeout=1200)

        corepo_content_service.start()
        hive.start()

        # Service is not ready until both are done:
        corepo_content_service.waitFor("was successfully deployed in")
        corepo_content_service.waitFor(") ready in ")

        hive.waitFor("Harvesting available records")

    def on_release(self, name, container):
        if name == "corepo-db":
            connector = PostgresDockerConnector(container)
            connector.wipe("records", "corepo")
            connector.restart_sequence("work_id_seq", "corepo")
            connector.restart_sequence("unit_id_seq", "corepo")

class ResourceManager( AbstractResourceManager ):

    def __init__( self, resource_folder, tests, use_preloaded, use_config, conf_file=None ):

        logger.info( "Securing necessary resources." )
        self.tests = tests

        self.resource_folder = resource_folder
        if not os.path.exists( self.resource_folder ):
            os.mkdir( self.resource_folder )

        self.use_preloaded_resources = use_preloaded

        self.use_config_resources = use_config

        self.resource_config = ConfigObj(self.use_config_resources)

        self.container_pool = ContainerPoolImpl(resource_folder)

        self.required_artifacts = {'wiremock-rules-openagency': ['wiremock-rules-openagency.zip', 'os-wiremock-rules']}
        for artifact in self.required_artifacts:
            self.required_artifacts[artifact].append(self._secure_artifact(artifact, *self.required_artifacts[artifact]))

        logger.info( "Fetch corepo-ingest from maven repository." )

        # Must use maven-dependency-plugin:2.8 to support get file
        exe_cmd = ["mvn",
                   "org.apache.maven.plugins:maven-dependency-plugin:2.8:get",
                   "-Ddest=%s" % self.resource_folder,
                   "-Dartifact=dk.dbc:corepo-ingest:1.1-SNAPSHOT",
                   "-DremoteRepositories=http://mavenrepo.dbc.dk/nexus/content/groups/public"]
        result = subprocess.Popen(exe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = result.communicate()
        if result.returncode != 0:
            die("Something went during maven call %s" % stdout + stderr)

    def shutdown(self):
        self.container_pool.shutdown()

    def _secure_artifact(self, name, artifact, project, build_number=None):
        if name in self.resource_config:
            logger.debug("configured resource %s at %s" % (name, self.resource_config[name]))
            return self.resource_config[name]

        if self.use_preloaded_resources == False:
            logger.debug( "Downloading %s artifact from integration server"%artifact )
            iserv = IServer( temp_folder=self.resource_folder, project_name=project )

            return iserv.download_and_validate_artifact( self.resource_folder, artifact, build_number=build_number)

        logger.debug("Using preloaded %s artifact"%artifact)
        preloaded_artifact = os.path.join(self.resource_folder, artifact)

        return preloaded_artifact

