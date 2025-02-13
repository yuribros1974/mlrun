# Copyright 2018 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import hashlib
import os
import warnings

import yaml

import mlrun
import mlrun.errors

from ..datastore import get_store_uri, is_store_uri, store_manager
from ..model import ModelObj
from ..utils import StorePrefix, calculate_local_file_hash, generate_artifact_uri

calc_hash = True


class ArtifactMetadata(ModelObj):
    _dict_fields = ["key", "project", "iter", "tree", "description", "hash", "tag"]
    _extra_fields = ["updated", "labels"]

    def __init__(
        self,
        key=None,
        project=None,
        iter=None,
        tree=None,
        description=None,
        hash=None,
        tag=None,
    ):
        self.key = key
        self.project = project
        self.iter = iter
        self.tree = tree
        self.description = description
        self.hash = hash
        self.labels = {}
        self.updated = None
        self.tag = tag  # temp store of the tag

    def base_dict(self):
        return super().to_dict()

    def to_dict(self, fields=None, exclude=None):
        """return long dict form of the artifact"""
        return super().to_dict(self._dict_fields + self._extra_fields, exclude=exclude)

    @classmethod
    def from_dict(cls, struct=None, fields=None, deprecated_fields: dict = None):
        fields = fields or cls._dict_fields + cls._extra_fields
        return super().from_dict(
            struct, fields=fields, deprecated_fields=deprecated_fields
        )


class ArtifactSpec(ModelObj):
    _dict_fields = [
        "src_path",
        "target_path",
        "viewer",
        "inline",
        "format",
        "size",
        "db_key",
        "extra_data",
    ]

    _extra_fields = ["annotations", "producer", "sources", "license", "encoding"]

    def __init__(
        self,
        src_path=None,
        target_path=None,
        viewer=None,
        is_inline=False,
        format=None,
        size=None,
        db_key=None,
        extra_data=None,
        body=None,
    ):
        self.src_path = src_path
        self.target_path = target_path
        self.viewer = viewer
        self._is_inline = is_inline
        self.format = format
        self.size = size
        self.db_key = db_key
        self.extra_data = extra_data or {}

        self._body = body
        self.encoding = None
        self.annotations = None
        self.sources = []
        self.producer = None
        self.license = ""

    def base_dict(self):
        return super().to_dict()

    def to_dict(self, fields=None, exclude=None):
        """return long dict form of the artifact"""
        return super().to_dict(self._dict_fields + self._extra_fields, exclude=exclude)

    @classmethod
    def from_dict(cls, struct=None, fields=None, deprecated_fields: dict = None):
        fields = fields or cls._dict_fields + cls._extra_fields
        return super().from_dict(
            struct, fields=fields, deprecated_fields=deprecated_fields
        )

    @property
    def inline(self):
        """inline data (body)"""

        if self._is_inline:
            return self.get_body()
        return None

    @inline.setter
    def inline(self, body):
        self._body = body

    def get_body(self):
        """get the artifact body when inline"""
        return self._body


class ArtifactStatus(ModelObj):
    _dict_fields = ["state"]

    def __init__(self):
        self.state = "created"

    def base_dict(self):
        return super().to_dict()


class Artifact(ModelObj):
    kind = "artifact"
    _dict_fields = ["kind", "metadata", "spec", "status"]

    _store_prefix = StorePrefix.Artifact

    def __init__(
        self,
        key=None,
        body=None,
        viewer=None,
        is_inline=False,
        format=None,
        size=None,
        target_path=None,
        # All params up until here are legacy params for compatibility with legacy artifacts.
        project=None,
        metadata: ArtifactMetadata = None,
        spec: ArtifactSpec = None,
    ):
        self._metadata = None
        self.metadata = metadata
        self._spec = None
        self.spec = spec

        self.metadata.key = key or self.metadata.key
        self.metadata.project = (
            project or mlrun.mlconf.default_project or self.metadata.project
        )
        self.spec.size = size or self.spec.size
        self.spec.target_path = target_path or self.spec.target_path
        self.spec.format = format or self.spec.format
        self.spec.viewer = viewer or self.spec.viewer

        if body:
            self.spec.inline = body
        self.spec._is_inline = is_inline or self.spec._is_inline

        self.status = ArtifactStatus()

    @property
    def metadata(self) -> ArtifactMetadata:
        return self._metadata

    @metadata.setter
    def metadata(self, metadata):
        self._metadata = self._verify_dict(metadata, "metadata", ArtifactMetadata)

    @property
    def spec(self) -> ArtifactSpec:
        return self._spec

    @spec.setter
    def spec(self, spec):
        self._spec = self._verify_dict(spec, "spec", ArtifactSpec)

    @property
    def status(self) -> ArtifactStatus:
        return self._status

    @status.setter
    def status(self, status):
        self._status = self._verify_dict(status, "status", ArtifactStatus)

    def before_log(self):
        pass

    @property
    def is_dir(self):
        """this is a directory"""
        return False

    @property
    def uri(self):
        """return artifact uri (store://..)"""
        return self.get_store_url()

    def to_dataitem(self):
        """return a DataItem object (if available) representing the artifact content"""
        uri = self.get_store_url()
        if uri:
            return mlrun.get_dataitem(uri)

    def get_body(self):
        """get the artifact body when inline"""
        return self.spec.get_body()

    def get_target_path(self):
        """get the absolute target path for the artifact"""
        return self.spec.target_path

    def get_store_url(self, with_tag=True, project=None):
        """get the artifact uri (store://..) with optional parameters"""
        tag = self.metadata.tree if with_tag else None
        uri = generate_artifact_uri(
            project or self.metadata.project, self.spec.db_key, tag, self.metadata.iter
        )
        return get_store_uri(self._store_prefix, uri)

    def base_dict(self):
        """return short dict form of the artifact"""
        struct = {"kind": self.kind}
        for field in ["metadata", "spec", "status"]:
            val = getattr(self, field, None)
            if val:
                struct[field] = val.base_dict()
        return struct

    def upload(self):
        """internal, upload to target store"""
        src_path = self.spec.src_path
        body = self.get_body()
        if body:
            self._upload_body(body)
        else:
            if src_path and os.path.isfile(src_path):
                self._upload_file(src_path)

    def _upload_body(self, body, target=None):
        if calc_hash:
            self.metadata.hash = blob_hash(body)
        self.spec.size = len(body)
        store_manager.object(url=target or self.spec.target_path).put(body)

    def _upload_file(self, src, target=None):
        if calc_hash:
            self.metadata.hash = calculate_local_file_hash(src)
        self.spec.size = os.stat(src).st_size
        store_manager.object(url=target or self.spec.target_path).upload(src)

    # Following properties are for backwards compatibility with the ArtifactLegacy class. They should be
    # removed once we only work with the new Artifact structure.

    @property
    def inline(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.inline instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.inline

    @inline.setter
    def inline(self, body):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.inline instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.inline = body

    @property
    def tag(self):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the metadata, use artifact.metadata.tag instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.metadata.tag

    @tag.setter
    def tag(self, tag):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the metadata, use artifact.metadata.tag instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.metadata.tag = tag

    @property
    def key(self):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the metadata, use artifact.metadata.key instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.metadata.key

    @key.setter
    def key(self, key):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the metadata, use artifact.metadata.key instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.metadata.key = key

    @property
    def src_path(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.src_path instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.src_path

    @src_path.setter
    def src_path(self, src_path):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.src_path instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.src_path = src_path

    @property
    def target_path(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.target_path instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.target_path

    @target_path.setter
    def target_path(self, target_path):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.target_path instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.target_path = target_path

    @property
    def producer(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.producer instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.producer

    @producer.setter
    def producer(self, producer):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.producer instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.producer = producer

    @property
    def format(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.format instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.format

    @format.setter
    def format(self, format):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.format instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.format = format

    @property
    def viewer(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.viewer instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.viewer

    @viewer.setter
    def viewer(self, viewer):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.viewer instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.viewer = viewer

    @property
    def size(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.size instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.size

    @size.setter
    def size(self, size):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.size instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.size = size

    @property
    def db_key(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.db_key instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.db_key

    @db_key.setter
    def db_key(self, db_key):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.db_key instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.db_key = db_key

    @property
    def sources(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.sources instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.sources

    @sources.setter
    def sources(self, sources):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.sources instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.sources = sources

    @property
    def extra_data(self):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.extra_data instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.spec.extra_data

    @extra_data.setter
    def extra_data(self, extra_data):
        """This is a property of the spec, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.spec.extra_data instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.spec.extra_data = extra_data

    @property
    def labels(self):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.metadata.labels instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.metadata.labels

    @labels.setter
    def labels(self, labels):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the metadata, use artifact.metadata.labels instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.metadata.labels = labels

    @property
    def iter(self):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.metadata.iter instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.metadata.iter

    @iter.setter
    def iter(self, iter):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the metadata, use artifact.metadata.iter instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.metadata.iter = iter

    @property
    def tree(self):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.metadata.tree instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.metadata.tree

    @tree.setter
    def tree(self, tree):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the metadata, use artifact.metadata.tree instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.metadata.tree = tree

    @property
    def project(self):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.metadata.project instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.metadata.project

    @project.setter
    def project(self, project):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the metadata, use artifact.metadata.project instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.metadata.project = project

    @property
    def hash(self):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the spec, use artifact.metadata.hash instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        return self.metadata.hash

    @hash.setter
    def hash(self, hash):
        """This is a property of the metadata, look there for documentation
        leaving here for backwards compatibility with users code that used ArtifactLegacy"""
        warnings.warn(
            "This is a property of the metadata, use artifact.metadata.hash instead"
            "This will be deprecated in 1.3.0, and will be removed in 1.5.0",
            # TODO: In 1.3.0 do changes in examples & demos In 1.5.0 remove
            PendingDeprecationWarning,
        )
        self.metadata.hash = hash


class DirArtifactSpec(ArtifactSpec):
    _dict_fields = [
        "src_path",
        "target_path",
        "db_key",
    ]


class DirArtifact(Artifact):
    kind = "dir"

    _dict_fields = [
        "key",
        "kind",
        "iter",
        "tree",
        "src_path",
        "target_path",
        "description",
        "db_key",
    ]

    @property
    def spec(self) -> DirArtifactSpec:
        return self._spec

    @spec.setter
    def spec(self, spec):
        self._spec = self._verify_dict(spec, "spec", DirArtifactSpec)

    @property
    def is_dir(self):
        return True

    def upload(self):
        if not self.spec.src_path:
            raise ValueError("local/source path not specified")

        files = os.listdir(self.spec.src_path)
        for f in files:
            file_path = os.path.join(self.spec.src_path, f)
            if not os.path.isfile(file_path):
                raise ValueError(f"file {file_path} not found, cant upload")
            target = os.path.join(self.spec.target_path, f)
            store_manager.object(url=target).upload(file_path)


class LinkArtifactSpec(ArtifactSpec):
    _dict_fields = ArtifactSpec._dict_fields + [
        "link_iteration",
        "link_key",
        "link_tree",
    ]

    def __init__(
        self,
        src_path=None,
        target_path=None,
        link_iteration=None,
        link_key=None,
        link_tree=None,
    ):
        super().__init__(src_path, target_path)
        self.link_iteration = link_iteration
        self.link_key = link_key
        self.link_tree = link_tree


class LinkArtifact(Artifact):
    kind = "link"

    def __init__(
        self,
        key=None,
        target_path="",
        link_iteration=None,
        link_key=None,
        link_tree=None,
        # All params up until here are legacy params for compatibility with legacy artifacts.
        project=None,
        metadata: ArtifactMetadata = None,
        spec: LinkArtifactSpec = None,
    ):
        super().__init__(
            key, target_path=target_path, project=project, metadata=metadata, spec=spec
        )
        self.spec.link_iteration = link_iteration
        self.spec.link_key = link_key
        self.spec.link_tree = link_tree

    @property
    def spec(self) -> LinkArtifactSpec:
        return self._spec

    @spec.setter
    def spec(self, spec):
        self._spec = self._verify_dict(spec, "spec", LinkArtifactSpec)


class LegacyArtifact(ModelObj):

    _dict_fields = [
        "key",
        "kind",
        "iter",
        "tree",
        "src_path",
        "target_path",
        "hash",
        "description",
        "viewer",
        "inline",
        "format",
        "size",
        "db_key",
        "extra_data",
        "tag",
    ]
    kind = ""
    _store_prefix = StorePrefix.Artifact

    def __init__(
        self,
        key=None,
        body=None,
        viewer=None,
        is_inline=False,
        format=None,
        size=None,
        target_path=None,
    ):
        self.key = key
        self.project = ""
        self.db_key = None
        self.size = size
        self.iter = None
        self.tree = None
        self.updated = None
        self.target_path = target_path
        self.src_path = None
        self._body = body
        self.format = format
        self.description = None
        self.viewer = viewer
        self.encoding = None
        self.labels = {}
        self.annotations = None
        self.sources = []
        self.producer = None
        self.hash = None
        self._inline = is_inline
        self.license = ""
        self.extra_data = {}
        self.tag = None  # temp store of the tag

    def before_log(self):
        for key, item in self.extra_data.items():
            if hasattr(item, "target_path"):
                self.extra_data[key] = item.target_path

    @property
    def is_dir(self):
        """this is a directory"""
        return False

    @property
    def inline(self):
        """inline data (body)"""
        if self._inline:
            return self.get_body()
        return None

    @inline.setter
    def inline(self, body):
        self._body = body

    @property
    def uri(self):
        """return artifact uri (store://..)"""
        return self.get_store_url()

    def to_dataitem(self):
        """return a DataItem object (if available) representing the artifact content"""
        uri = self.get_store_url()
        if uri:
            return mlrun.get_dataitem(uri)

    def get_body(self):
        """get the artifact body when inline"""
        return self._body

    def get_target_path(self):
        """get the absolute target path for the artifact"""
        return self.target_path

    def get_store_url(self, with_tag=True, project=None):
        """get the artifact uri (store://..) with optional parameters"""
        tag = self.tree if with_tag else None
        uri = generate_artifact_uri(
            project or self.project, self.db_key, tag, self.iter
        )
        return get_store_uri(self._store_prefix, uri)

    def base_dict(self):
        """return short dict form of the artifact"""
        return super().to_dict()

    def to_dict(self, fields=None):
        """return long dict form of the artifact"""
        return super().to_dict(
            self._dict_fields
            + ["updated", "labels", "annotations", "producer", "sources", "project"]
        )

    @classmethod
    def from_dict(cls, struct=None, fields=None):
        fields = fields or cls._dict_fields + [
            "updated",
            "labels",
            "annotations",
            "producer",
            "sources",
            "project",
        ]
        return super().from_dict(struct, fields=fields)

    def upload(self):
        """internal, upload to target store"""
        src_path = self.src_path
        body = self.get_body()
        if body:
            self._upload_body(body)
        else:
            if src_path and os.path.isfile(src_path):
                self._upload_file(src_path)

    def _upload_body(self, body, target=None):
        if calc_hash:
            self.hash = blob_hash(body)
        self.size = len(body)
        store_manager.object(url=target or self.target_path).put(body)

    def _upload_file(self, src, target=None):
        if calc_hash:
            self.hash = calculate_local_file_hash(src)
        self.size = os.stat(src).st_size
        store_manager.object(url=target or self.target_path).upload(src)

    def artifact_kind(self):
        return self.kind


class LegacyDirArtifact(LegacyArtifact):
    _dict_fields = [
        "key",
        "kind",
        "iter",
        "tree",
        "src_path",
        "target_path",
        "description",
        "db_key",
    ]
    kind = "dir"

    @property
    def is_dir(self):
        return True

    def upload(self):
        if not self.src_path:
            raise ValueError("local/source path not specified")

        files = os.listdir(self.src_path)
        for f in files:
            file_path = os.path.join(self.src_path, f)
            if not os.path.isfile(file_path):
                raise ValueError(f"file {file_path} not found, cant upload")
            target = os.path.join(self.target_path, f)
            store_manager.object(url=target).upload(file_path)


class LegacyLinkArtifact(LegacyArtifact):
    _dict_fields = LegacyArtifact._dict_fields + [
        "link_iteration",
        "link_key",
        "link_tree",
    ]
    kind = "link"

    def __init__(
        self,
        key=None,
        target_path="",
        link_iteration=None,
        link_key=None,
        link_tree=None,
    ):

        super().__init__(key)
        self.target_path = target_path
        self.link_iteration = link_iteration
        self.link_key = link_key
        self.link_tree = link_tree


def blob_hash(data):
    if isinstance(data, str):
        data = data.encode()
    h = hashlib.sha1()
    h.update(data)
    return h.hexdigest()


def upload_extra_data(
    artifact_spec: Artifact,
    extra_data: dict,
    prefix="",
    update_spec=False,
):
    if not extra_data:
        return
    target_path = artifact_spec.target_path
    for key, item in extra_data.items():

        if isinstance(item, bytes):
            target = os.path.join(target_path, key)
            store_manager.object(url=target).put(item)
            artifact_spec.extra_data[prefix + key] = target
            continue

        if not (item.startswith("/") or "://" in item):
            src_path = (
                os.path.join(artifact_spec.src_path, item)
                if artifact_spec.src_path
                else item
            )
            if not os.path.isfile(src_path):
                raise ValueError(f"extra data file {src_path} not found")
            target = os.path.join(target_path, item)
            store_manager.object(url=target).upload(src_path)

        if update_spec:
            artifact_spec.extra_data[prefix + key] = item


def get_artifact_meta(artifact):
    """return artifact object, and list of extra data items


    :param artifact:   artifact path (store://..) or DataItem

    :returns: artifact object, extra data dict

    """
    if hasattr(artifact, "artifact_url"):
        artifact = artifact.artifact_url

    if is_store_uri(artifact):
        artifact_spec, target = store_manager.get_store_artifact(artifact)

    elif artifact.lower().endswith(".yaml"):
        data = store_manager.object(url=artifact).get()
        spec = yaml.load(data, Loader=yaml.FullLoader)
        artifact_spec = mlrun.artifacts.dict_to_artifact(spec)

    else:
        raise ValueError(f"cant resolve artifact file for {artifact}")

    extra_dataitems = {}
    for k, v in artifact_spec.extra_data.items():
        extra_dataitems[k] = store_manager.object(v, key=k)

    return artifact_spec, extra_dataitems
