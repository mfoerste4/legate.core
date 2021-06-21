# Copyright 2021 NVIDIA Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from enum import IntEnum

import numpy as np

from .legion import (
    ArgumentMap,
    BufferBuilder,
    Future,
    IndexTask,
    Task as SingleTask,
)


class Permission(IntEnum):
    NO_ACCESS = 0
    READ = 1
    WRITE = 2
    READ_WRITE = 3
    REDUCTION = 4


class ScalarArg(object):
    _serializers = {
        bool: BufferBuilder.pack_bool,
        np.int8: BufferBuilder.pack_8bit_int,
        np.int16: BufferBuilder.pack_16bit_int,
        np.int32: BufferBuilder.pack_32bit_int,
        np.int64: BufferBuilder.pack_64bit_int,
        np.uint8: BufferBuilder.pack_8bit_uint,
        np.uint16: BufferBuilder.pack_16bit_uint,
        np.uint32: BufferBuilder.pack_32bit_uint,
        np.uint64: BufferBuilder.pack_64bit_uint,
        np.float32: BufferBuilder.pack_32bit_float,
        np.float64: BufferBuilder.pack_64bit_float,
    }

    def __init__(self, value, dtype):
        self._value = value
        self._dtype = dtype

    def pack(self, buf):
        if isinstance(self._dtype, tuple) or isinstance(self._dtype, list):
            assert len(self._dtype) == 1
            dtype = self._dtype[0]
            self._serializers[np.int32](buf, len(self._value))
            serializer = self._serializers[dtype]
            for value in self._value:
                serializer(buf, value)
        elif self._dtype in self._serializers:
            self._serializers[self._dtype](buf, self._value)
        else:
            raise ValueError("Unsupported data type: %s" % str(self._dtype))


class DtypeArg(object):
    def __init__(self, dtype):
        self._dtype = dtype

    def pack(self, buf):
        buf.pack_dtype(self._dtype)


class PointArg(object):
    def __init__(self, point, untyped=False):
        self._point = point
        self._untyped = untyped

    def pack(self, buf):
        if self._untyped:
            buf.pack_32bit_int(len(self._point))
        buf.pack_point(self._point)


class RegionFieldArg(object):
    def __init__(self, op, dim, redop, key, field_id, transform):
        self._op = op
        self._dim = dim
        self._redop = redop
        self._key = key
        self._field_id = field_id
        self._transform = transform

    def pack(self, buf):
        dim = self._dim if self._transform is None else self._transform.N
        buf.pack_32bit_int(dim)
        buf.pack_32bit_int(-1 if self._redop is None else self._redop)
        buf.pack_32bit_uint(
            self._op.get_requirement_index(self._key, self._field_id)
        )
        buf.pack_32bit_uint(self._field_id)
        if self._transform is not None:
            buf.pack_32bit_int(self._transform.M)
            buf.pack_32bit_int(self._transform.N)
            for x in range(0, self._transform.M):
                for y in range(0, self._transform.N):
                    buf.pack_64bit_int(self._transform.trans[x, y])
            for x in range(0, self._transform.M):
                buf.pack_64bit_int(self._transform.offset[x])
        else:
            buf.pack_32bit_int(-1)


_single_task_calls = {
    Permission.NO_ACCESS: SingleTask.add_no_access_requirement,
    Permission.READ: SingleTask.add_read_requirement,
    Permission.WRITE: SingleTask.add_write_requirement,
    Permission.READ_WRITE: SingleTask.add_read_write_requirement,
    Permission.REDUCTION: SingleTask.add_reduction_requirement,
}

_index_task_calls = {
    Permission.NO_ACCESS: IndexTask.add_no_access_requirement,
    Permission.READ: IndexTask.add_read_requirement,
    Permission.WRITE: IndexTask.add_write_requirement,
    Permission.READ_WRITE: IndexTask.add_read_write_requirement,
    Permission.REDUCTION: IndexTask.add_reduction_requirement,
}


class Broadcast(object):
    def __init__(self, redop=None):
        self.redop = redop

    def add(self, task, req, fields):
        f = _index_task_calls[req.permission]
        if self.redop is None:
            f(task, req.region, fields, 0, parent=req.region, tag=req.tag)
        else:
            assert req.permission == Permission.REDUCTION
            f(
                task,
                req.region,
                fields,
                self.redop,
                0,
                parent=req.region,
                tag=req.tag,
            )

    def add_single(self, task, req, fields):
        f = _single_task_calls[req.permission]
        if self.redop is None:
            f(task, req.region, fields, tag=req.tag, flags=req.flags)
        else:
            assert req.permission == Permission.REDUCTION
            f(
                task,
                req.region,
                fields,
                self.redop,
                tag=req.tag,
                flags=req.flags,
            )

    def __hash__(self):
        return hash(("Broadcast", self.redop))

    def __eq__(self, other):
        return isinstance(other, Broadcast) and self.redop == other.redop


class Project(object):
    def __init__(self, part, proj=0, redop=None):
        self.part = part
        self.proj = proj
        self.redop = redop

    def add(self, task, req, fields):
        f = _index_task_calls[req.permission]
        if self.redop is None:
            f(task, self.part, fields, self.proj, tag=req.tag, flags=req.flags)
        else:
            assert req.permission == Permission.REDUCTION
            f(
                task,
                self.part,
                fields,
                self.redop,
                self.proj,
                tag=req.tag,
                flags=req.flags,
            )

    def add_single(self, task, req, fields):
        f = _single_task_calls[req.permission]
        if self.redop is None:
            f(task, req.region, fields, tag=req.tag)
        else:
            assert req.permission == Permission.REDUCTION
            f(task, req.region, fields, self.redop, tag=req.tag)

    def __hash__(self):
        return hash((self.part, self.proj, self.redop))

    def __repr__(self):
        return repr((self.part, self.proj, self.redop))

    def __eq__(self, other):
        return (
            isinstance(other, Project)
            and self.part == other.part
            and self.proj == other.proj
            and self.redop == other.redop
        )


class RegionReq(object):
    def __init__(self, region, permission, proj, tag, flags):
        self.region = region
        self.permission = permission
        self.proj = proj
        self.tag = tag
        self.flags = flags

    def __repr__(self):
        return repr(
            (self.region, self.permission, self.proj, self.tag, self.flags)
        )

    def __hash__(self):
        return hash(
            (self.region, self.proj, self.permission, self.tag, self.flags)
        )

    def __eq__(self, other):
        return (
            self.region == other.region
            and self.proj == other.proj
            and self.permission == other.permission
            and self.tag == other.tag
            and self.flags == other.flags
        )


class ProjectionSet(object):
    def __init__(self):
        self._entries = {}

    def _create(self, perm, entry):
        self._entries[perm] = set([entry])

    def _update(self, perm, entry):
        entries = self._entries[perm]
        entries.add(entry)
        if perm == Permission.WRITE and len(entries) > 1:
            raise ValueError("Interfering requirements found")

    def insert(self, perm, proj_info):
        if perm == Permission.READ_WRITE:
            self.insert(Permission.READ, proj_info)
            self.insert(Permission.WRITE, proj_info)
        else:
            if perm in self._entries:
                self._update(perm, proj_info)
            else:
                self._create(perm, proj_info)

    def coalesce(self):
        if len(self._entries) == 1:
            perm = list(self._entries.keys())[0]
            return [(perm, *entry) for entry in self._entries[perm]]
        all_perms = set(self._entries.keys())
        # If the fields is requested with conflicting permissions,
        # promote them to read write permission.
        if len(all_perms - set([Permission.NO_ACCESS])) > 1:
            perm = Permission.READ_WRITE

            # When the field requires read write permission,
            # all projections must be the same
            all_entries = set()
            for entry in self._entries.values():
                all_entries = all_entries | entry
            if len(all_entries) > 1:
                raise ValueError(
                    f"Interfering requirements found: {all_entries}"
                )

            return [(perm, *all_entries.pop())]

        # This can happen when there is a no access requirement.
        # For now, we don't coalesce it with others.
        else:
            return [pair for pair in self._entries.items()]

    def __repr__(self):
        return str(self._entries)


class FieldSet(object):
    def __init__(self):
        self._fields = {}

    def insert(self, field_id, perm, proj_info):
        if field_id in self._fields:
            proj_set = self._fields[field_id]
        else:
            proj_set = ProjectionSet()
            self._fields[field_id] = proj_set
        proj_set.insert(perm, proj_info)

    def coalesce(self):
        coalesced = {}
        for field_id, proj_set in self._fields.items():
            proj_infos = proj_set.coalesce()
            for key in proj_infos:
                if key in coalesced:
                    coalesced[key].append(field_id)
                else:
                    coalesced[key] = [field_id]

        return coalesced


class TaskLauncher(object):
    def __init__(self, context, task_id, mapper_id=0, tag=0):
        assert type(tag) != bool
        self._context = context
        self._task_id = task_id
        self._mapper_id = mapper_id
        self._args = list()
        self._region_args = {}
        self._region_reqs = list()
        self._region_reqs_indices = {}
        self._next_region_idx = 0
        self._projections = list()
        self._future_args = list()
        self._future_map_args = list()
        self._tag = tag
        self._sharding_space = None
        self._point = None

    @property
    def library_task_id(self):
        return self._task_id

    @property
    def library_mapper_id(self):
        return self._mapper_id

    @property
    def legion_task_id(self):
        return self._context.get_task_id(self._task_id)

    @property
    def legion_mapper_id(self):
        return self._context.get_mapper_id(self._mapper_id)

    def __del__(self):
        self._region_args.clear()
        self._projections.clear()
        self._future_args.clear()
        self._future_map_args.clear()

    def _coalesce_region_requirements(self):
        for region, field_set in self._region_args.items():
            perm_map = field_set.coalesce()
            for key, fields in perm_map.items():
                req_idx = len(self._region_reqs)
                req = RegionReq(region, *key)
                for field_id in fields:
                    self._region_reqs_indices[(req, field_id)] = req_idx
                self._region_reqs.append((req, fields))

    def add_scalar_arg(self, value, dtype):
        self._args.append(ScalarArg(value, dtype))

    def add_dtype_arg(self, dtype):
        self._args.append(DtypeArg(dtype))

    def get_requirement_index(self, key, field_id):
        try:
            return self._region_reqs_indices[(key, field_id)]
        except KeyError:
            key = RegionReq(
                key.region, Permission.READ_WRITE, key.proj, key.tag, key.flags
            )
            return self._region_reqs_indices[(key, field_id)]

    def add_store(self, store, proj, perm, tag, flags):
        scalar = store.kind == Future
        self.add_scalar_arg(scalar, bool)
        self.add_scalar_arg(store.ndim, np.int32)
        self.add_dtype_arg(store.type)
        self.add_shape(store.shape)

        if scalar:
            if perm != Permission.READ:
                raise ValueError("Scalar stores must be read only")
            self.add_future(store.storage)
            return

        region = store.storage.region
        field_id = store.storage.field.field_id
        transform = store.get_accessor_transform()

        if region in self._region_args:
            field_set = self._region_args[region]
        else:
            field_set = FieldSet()
            self._region_args[region] = field_set
        proj_info = (proj, tag, flags)
        field_set.insert(field_id, perm, proj_info)

        self._args.append(
            RegionFieldArg(
                self,
                region.index_space.get_dim(),
                proj.redop,
                RegionReq(region, perm, *proj_info),
                field_id,
                transform,
            )
        )

    def add_no_access(self, store, proj, tag=0, flags=0):
        self.add_store(store, proj, Permission.NO_ACCESS, tag, flags)

    def add_input(self, store, proj, tag=0, flags=0):
        self.add_store(store, proj, Permission.READ, tag, flags)

    def add_output(self, store, proj, tag=0, flags=0):
        self.add_store(store, proj, Permission.WRITE, tag, flags)

    def add_inout(self, store, proj, tag=0, flags=0):
        self.add_store(store, proj, Permission.READ_WRITE, tag, flags)

    def add_reduction(self, store, proj, tag=0, flags=0):
        self.add_store(store, proj, Permission.REDUCTION, tag, flags)

    def add_future(self, future):
        self._future_args.append(future)

    def add_future_map(self, future_map):
        self._future_map_args.append(future_map)

    def add_point(self, point, untyped=False):
        self._args.append(PointArg(point, untyped))

    def add_shape(self, shape, chunk_shape=None, proj=None):
        assert chunk_shape is None or len(shape) == len(chunk_shape)
        self.add_scalar_arg(shape, (np.int64,))
        if chunk_shape is not None:
            assert proj is not None
            self.add_scalar_arg(proj, np.int32)
            self.add_scalar_arg(chunk_shape, (np.int64,))
        else:
            assert proj is None
            self.add_scalar_arg(-1, np.int32)

    def set_sharding_space(self, space):
        self._sharding_space = space

    def set_point(self, point):
        self._point = point

    def build_task(self, launch_domain, argbuf):
        self._coalesce_region_requirements()

        for arg in self._args:
            arg.pack(argbuf)
        task = IndexTask(
            self.legion_task_id,
            launch_domain,
            self._context.empty_argmap,
            argbuf.get_string(),
            argbuf.get_size(),
            mapper=self.legion_mapper_id,
            tag=self._tag,
        )
        if self._sharding_space is not None:
            task.set_sharding_space(self._sharding_space)

        for (req, fields) in self._region_reqs:
            req.proj.add(task, req, fields)
        for future in self._future_args:
            task.add_future(future)
        for future_map in self._future_map_args:
            task.add_point_future(ArgumentMap(future_map=future_map))
        return task

    def build_single_task(self, argbuf):
        self._coalesce_region_requirements()

        for arg in self._args:
            arg.pack(argbuf)
        task = SingleTask(
            self.legion_task_id,
            argbuf.get_string(),
            argbuf.get_size(),
            mapper=self.legion_mapper_id,
            tag=self._tag,
        )
        for (req, fields) in self._region_reqs:
            req.proj.add_single(task, req, fields)
        for future in self._future_args:
            task.add_future(future)
        if len(self._region_args) == 0:
            task.set_local_function(True)
        if self._sharding_space is not None:
            task.set_sharding_space(self._sharding_space)
        if self._point is not None:
            task.set_point(self._point)
        return task

    def execute(self, launch_domain, redop=None):
        # Note that we should hold a reference to this buffer
        # until we launch a task, otherwise the Python GC will
        # collect the Python object holding the buffer, which
        # in turn will deallocate the C side buffer.
        argbuf = BufferBuilder()
        task = self.build_task(launch_domain, argbuf)
        if redop is not None:
            return self._context.dispatch(task, redop=redop)
        else:
            return self._context.dispatch(task)

    def execute_single(self):
        argbuf = BufferBuilder()
        return self._context.dispatch(self.build_single_task(argbuf))
