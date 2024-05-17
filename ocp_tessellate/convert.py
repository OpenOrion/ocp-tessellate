import enum
from hashlib import sha256

from ocp_tessellate.cad_objects import CoordAxis, CoordSystem, OcpGroup, OcpObject
from ocp_tessellate.defaults import get_default, preset
from ocp_tessellate.ocp_utils import *
from ocp_tessellate.tessellator import (
    compute_quality,
    convert_vertices,
    discretize_edges,
    tessellate,
)
from ocp_tessellate.utils import *

LINE_WIDTH = 2
POINT_SIZE = 4

DEBUG = True


def _debug(level, msg, name=None, prefix="debug:", end="\n"):
    if DEBUG:
        prefix = "  " * level + prefix
        suffix = f" ('{name}')" if name is not None else ""
        print(f"{prefix} {msg} {suffix}", end=end, flush=True)


def get_name(obj, name, default):
    if name is None:
        if hasattr(obj, "name") and obj.name is not None and obj.name != "":
            name = obj.name
        elif hasattr(obj, "label") and obj.label is not None and obj.label != "":
            name = obj.label
        else:
            name = default
    return name


def get_type(obj):
    kinds = {
        "TopoDS_Edge": "Edge",
        "TopoDS_Face": "Face",
        "TopoDS_Shell": "Shell",
        "TopoDS_Solid": "Solid",
        "TopoDS_Vertex": "Vertex",
        "TopoDS_Wire": "Wire",
    }
    typ = kinds.get(class_name(obj))
    if typ is None:
        raise ValueError(f"Unknown type: {type(obj)}")
    return typ


def get_kind(typ):
    kinds = {
        "Edge": "edge",
        "Face": "face",
        "Shell": "face",
        "Solid": "solid",
        "Vertex": "vertex",
        "Wire": "edge",
    }
    kind = kinds.get(typ)
    if kind is None:
        raise ValueError(f"Unknown type: {typ}")
    return kind


def unwrap(obj):
    if hasattr(obj, "wrapped"):
        return obj.wrapped
    elif isinstance(obj, (list, tuple)):
        return [(x.wrapped if hasattr(x, "wrapped") else x) for x in obj]
    return obj


def get_accuracies(shapes):
    def _get_accuracies(shapes, lengths):
        if shapes.get("parts"):
            for shape in shapes["parts"]:
                _get_accuracies(shape, lengths)
        elif shapes.get("type") == "shapes":
            accuracies[shapes["id"]] = shapes["accuracy"]

    accuracies = {}
    _get_accuracies(shapes, accuracies)
    return accuracies


def get_normal_len(render_normals, shapes, deviation):
    if render_normals:
        accuracies = get_accuracies(shapes)
        normal_len = max(accuracies.values()) / deviation * 4
    else:
        normal_len = 0

    return normal_len


def get_color_for_object(obj, color=None, alpha=None, kind=None):
    default_colors = {
        # ocp types
        "TopoDS_Edge": "MediumOrchid",
        "TopoDS_Face": "Violet",
        "TopoDS_Shell": "Violet",
        "TopoDS_Solid": (232, 176, 36),
        "TopoDS_Vertex": "MediumOrchid",
        "TopoDS_Wire": "MediumOrchid",
        # kind of objects
        "edge": "MediumOrchid",
        "wire": "MediumOrchid",
        "face": "Violet",
        "shell": "Violet",
        "solid": (232, 176, 36),
        "vertex": "MediumOrchid",
    }

    if color is not None:
        col_a = Color(color)

    elif hasattr(obj, "color") and obj.color is not None:
        col_a = Color(obj.color)

    # elif color is None and is_topods_compound(obj) and kind is not None:
    elif color is None and kind is not None:
        col_a = Color(default_colors[kind])

    # else return default color
    else:
        col_a = Color(default_colors.get(class_name(unwrap(obj))))

    if alpha is not None:
        col_a.a = alpha

    return col_a


def create_cache_id(obj):
    sha = sha256()
    objs = [obj] if not isinstance(obj, (tuple, list)) else obj
    for o in objs:
        sha.update(serialize(o.wrapped if is_wrapped(o) else o))

    return sha.hexdigest()


# TODOs:
# - cache handling
# - ImageFace
# - render mates
# - render joints
# - render parent
# - render normals
#
# - CadQuery objects
# - CadQuery assemblies


class OcpConverter:
    def __init__(self, progress=None):
        self.instances = []
        self.ocp = None
        self.progress = progress

    def get_instance(self, obj, kind, name, color, cache_id):
        is_instance = False
        ocp_obj = None

        # Create the relocated object as a copy
        loc = obj.Location()  # Get location
        obj2 = downcast(obj.Moved(loc.Inverted()))

        # check if the same instance is already available
        for i, instance in enumerate(self.instances):
            if instance[0] == obj2:
                # create an OcpObject referencing instance i
                ocp_obj = OcpObject(
                    kind,
                    ref=i,
                    name=name,
                    loc=loc,
                    color=color,
                    cache_id=cache_id,
                )
                # and stop the loop
                is_instance = True

                if self.progress is not None:
                    self.progress.update("-")

                break

        if not is_instance:
            ref = len(self.instances)
            # append the new instance
            self.instances.append((obj2, cache_id))
            # and create a referential OcpObject
            ocp_obj = OcpObject(
                kind,
                ref=ref,
                name=name,
                loc=loc,
                color=color,
                cache_id=cache_id,
            )

        return ocp_obj

    def unify(self, objs, kind, name, color, alpha=1.0):
        # Try to downcast to one TopoDS_Shape
        if len(objs) == 1:
            ocp_obj = objs[0]
            # unroll TopoDS_Compound
            if is_topods_compound(ocp_obj):
                ocp_objs = list(list_topods_compound(ocp_obj))
                if len(ocp_objs) == 1:
                    ocp_obj = downcast(ocp_objs[0])
                elif kind in ["edge", "vertex"]:
                    ocp_obj = ocp_objs

        # else make a TopoDS_Compound
        elif kind in ["solid", "face", "shell"]:
            ocp_obj = make_compound(objs)

        # and for vertices and edges, keep the list
        else:
            ocp_obj = objs

        color = get_color_for_object(
            ocp_obj[0] if isinstance(ocp_obj, list) else ocp_obj,
            color,
            kind=kind,
        )
        if alpha < 1.0:
            color.a = alpha

        if kind in ("solid", "face", "shell"):
            return self.get_instance(
                ocp_obj,
                kind,
                name,
                color,
                cache_id=create_cache_id(objs),
            )
        else:
            return OcpObject(
                kind,
                obj=ocp_obj,
                name=name,
                color=color,
                width=LINE_WIDTH if kind == "edge" else POINT_SIZE,
            )

    def handle_list_tuple(
        self, cad_obj, obj_name, rgba_color, sketch_local, helper_scale, level
    ):
        _debug(level, "handle_list_tuple", obj_name)

        ocp_obj = OcpGroup(name=get_name(cad_obj, obj_name, "List"))
        for obj in cad_obj:
            name = get_name(cad_obj, obj_name, type_name(obj))

            result = self.to_ocp(
                obj,
                names=[name],
                colors=[rgba_color],
                sketch_local=sketch_local,
                helper_scale=helper_scale,
                top_level=False,
                level=level + 1,
            )
            ocp_obj.add(result.cleanup())

        return ocp_obj.make_unique_names().cleanup()

    def handle_compound(
        self, cad_obj, obj_name, rgba_color, sketch_local, helper_scale, level
    ):
        _debug(level, f"handle_compound", obj_name)

        if is_compound(cad_obj):
            cad_obj = list(cad_obj)
        elif is_topods_compound(cad_obj):
            cad_obj = list(list_topods_compound(cad_obj))

        ocp_obj = OcpGroup(name=get_name(cad_obj, obj_name, "Compound"))
        for obj in cad_obj:
            result = self.to_ocp(
                obj,
                colors=[rgba_color],
                sketch_local=sketch_local,
                helper_scale=helper_scale,
                top_level=False,
                level=level + 1,
            )

            ocp_obj.add(result.cleanup())

        return ocp_obj.make_unique_names()

    def handle_dict(
        self, cad_obj, obj_name, rgba_color, sketch_local, helper_scale, level
    ):
        _debug(level, "handle_dict", obj_name)

        ocp_obj = OcpGroup(name=get_name(cad_obj, obj_name, "Dict"))
        for name, el in cad_obj.items():
            result = self.to_ocp(
                el,
                names=[name],
                colors=[rgba_color],
                sketch_local=sketch_local,
                top_level=False,
                helper_scale=helper_scale,
                level=level + 1,
            )
            ocp_obj.add(result.cleanup())

        return ocp_obj

    def handle_build123d_assembly(
        self, cad_obj, obj_name, rgba_color, helper_scale, sketch_local, level
    ):
        # TODO: Fix global location
        _debug(level, "handle_build123d_assembly", obj_name)

        name = get_name(cad_obj, obj_name, "Assembly")
        ocp_obj = OcpGroup(name=name, loc=get_location(cad_obj, as_none=False))

        for child in cad_obj.children:
            sub_obj = self.to_ocp(
                child,
                names=[child.label],
                helper_scale=helper_scale,
                top_level=False,
                level=level + 1,
            )
            if isinstance(sub_obj, OcpGroup) and sub_obj.length == 1:
                sub_obj.objects[0].loc = mul_locations(
                    sub_obj.objects[0].loc, sub_obj.loc
                )
                # if sub_obj.objects[0].loc is None:
                #     sub_obj.objects[0].loc = sub_obj.loc
                # else:
                #     sub_obj.objects[0].loc = (
                #         sub_obj.loc * sub_obj.objects[0].loc
                #     )
                sub_obj = sub_obj.objects[0]

            ocp_obj.add(sub_obj)
        return ocp_obj

    def handle_shapelist(
        self,
        cad_obj,
        obj_name,
        rgba_color,
        sketch_local,
        level,
    ):
        if is_build123d_shapelist(cad_obj):
            _debug(level, "handle_shapelist (build123d ShapeList)", obj_name)
            name = "ShapeList"
        else:
            _debug(level, "handle_shapelist (cadquery Workplane)", obj_name)
            name = "Workplane"

            # Resolve cadquery Workplane
            cad_obj = cad_obj.vals()
            if len(cad_obj) > 0 and is_topods_compound(cad_obj[0].wrapped):
                cad_obj = flatten([list(obj) for obj in cad_obj])

        # convert wires to edges
        if len(cad_obj) > 0 and is_topods_wire(cad_obj[0].wrapped):
            objs = [e.wrapped for o in cad_obj for e in o.edges()]
            typ = "Wire"

        # unwrap everything else
        else:
            objs = unwrap(cad_obj)
            typ = get_type(objs[0])

        kind = get_kind(typ)

        if kind in ("solid", "face", "shell"):
            ocp_obj = self.unify(
                objs,
                kind=kind,
                name=get_name(cad_obj, obj_name, f"{name}({typ})"),
                color=get_color_for_object(objs[0], rgba_color),
            )
        else:
            # keep the array of wrapped edges or vertices
            ocp_obj = OcpObject(
                kind,
                obj=objs,
                name=get_name(cad_obj, obj_name, f"{name}({typ})"),
                color=get_color_for_object(objs[0], rgba_color),
                width=LINE_WIDTH if kind == "edge" else POINT_SIZE,
            )
        return ocp_obj

    def handle_build123d_builder(
        self, cad_obj, obj_name, rgba_color, sketch_local, level
    ):
        _debug(level, f"handle_build123d_builder {cad_obj._obj_name}", obj_name)

        # bild123d BuildPart().part
        if is_build123d_part(cad_obj):
            typ, objs = "Solid", [cad_obj.part.wrapped]

        # build123d BuildSketch().sketch
        elif is_build123d_sketch(cad_obj):
            ocp_objs = cad_obj.sketch.faces()
            if len(ocp_objs) == 1:
                objs = [ocp_objs[0].wrapped]
            else:
                objs = [cad_obj.sketch.wrapped]
            typ = "Face"

        # build123d BuildLine().line
        elif is_build123d_line(cad_obj):
            typ, objs = "Edge", unwrap(cad_obj.edges())

        else:
            raise ValueError(f"Unknown build123d object: {cad_obj}")

        name = get_name(cad_obj, obj_name, typ)
        ocp_obj = self.unify(
            objs,
            kind=get_kind(typ),
            name=name,
            color=rgba_color,
        )

        if sketch_local and hasattr(cad_obj, "sketch_local"):
            ocp_obj.name = "sketch"
            ocp_obj = OcpGroup([ocp_obj], name=name)
            ocp_objs = cad_obj.sketch_local.faces()
            if len(ocp_objs) == 1:
                objs = [ocp_objs[0].wrapped]
            else:
                objs = [cad_obj.sketch_local.wrapped]
            ocp_obj.add(
                self.unify(
                    objs,
                    kind="face",
                    name="sketch_local",
                    color=rgba_color,
                    alpha=0.2,
                )
            )

        return ocp_obj

    def handle_shapes(self, cad_obj, obj_name, rgba_color, sketch_local, level):

        if is_topods_shape(cad_obj):
            t, obj = "TopoDS_Shape", downcast(cad_obj)
        elif is_build123d_shape(cad_obj):
            t, obj = "build123d Shape", cad_obj.wrapped
        elif is_cadquery_shape(cad_obj):
            t, obj = "cadquery Shape", cad_obj.wrapped
        else:
            raise ValueError(f"Unknown shape type: {cad_obj}")

        _debug(level, f"handle_shapes ({t}) ({class_name(obj)})", obj_name)

        edges = None
        if is_topods_wire(obj):
            typ, edges = "Wire", list(get_edges(obj))
        elif is_topods_compound(obj):
            typ = get_compound_type(obj)
            if typ == "Wire":
                obj = list(get_edges(obj))
        else:
            typ = type_name(obj)

        name = get_name(cad_obj, obj_name, typ)

        ocp_obj = self.unify(
            [obj] if edges is None else edges,
            kind=get_kind(typ),
            name=name,
            color=rgba_color,
        )
        return ocp_obj

    def handle_cadquery_sketch(self, cad_obj, obj_name, rgba_color, level):
        cad_objs = []
        for objs, calc_bb in [
            (cad_obj._faces, False),
            (cad_obj._edges, False),
            (cad_obj._wires, True),
            (cad_obj._selection, False),
        ]:
            if objs:
                c_objs = (
                    [None] * len(objs)
                    if is_toploc_location(list(objs)[0].wrapped)
                    else cad_obj.locs
                )
                cad_objs += [
                    (
                        o.wrapped
                        if is_toploc_location(o.wrapped)
                        else downcast(o.wrapped.Moved(loc.wrapped))
                    )
                    for o, loc in zip(list(objs), c_objs)
                ]
            if calc_bb:
                bb = bounding_box(make_compound(cad_objs))
                size = max(bb.xsize, bb.ysize, bb.zsize)

        name = get_name(cad_obj, obj_name, "Sketch")
        return self.to_ocp(
            cad_objs,
            names=[name],
            colors=[rgba_color],
            level=level,
            helper_scale=size / 20,
        )

    def handle_locations_planes(
        self, cad_obj, obj_name, rgba_color, helper_scale, sketch_local, level
    ):
        if is_build123d_location(cad_obj) or is_toploc_location(cad_obj):
            _debug(level, "build123d Location or TopLoc_Location", obj_name)

        elif (
            is_build123d_plane(cad_obj)
            and hasattr(cad_obj, "location")
            or is_gp_plane(cad_obj)
        ):
            _debug(level, "build123d Plane or gp_Pln", obj_name)

        elif is_cadquery_empty_workplane(cad_obj):
            _debug(level, "cadquery Workplane", obj_name)

        if is_build123d_plane(cad_obj) and hasattr(cad_obj, "location"):
            cad_obj = cad_obj.location
            def_name = "Plane"

        elif is_gp_plane(cad_obj):
            def_name = "Plane"
            cad_obj = loc_from_gp_pln(cad_obj)

        elif is_cadquery_empty_workplane(cad_obj):
            def_name = "Workplane"
            cad_obj = cad_obj.plane.location

        else:
            def_name = "Location"

        coord = get_location_coord(
            cad_obj.wrapped if is_build123d_location(cad_obj) else cad_obj
        )
        name = get_name(cad_obj, obj_name, def_name)
        ocp_obj = CoordSystem(
            name,
            coord["origin"],
            coord["x_dir"],
            coord["z_dir"],
            size=helper_scale,
        )
        return ocp_obj

    def handle_axis(
        self, cad_obj, obj_name, rgba_color, helper_scale, sketch_local, level
    ):
        _debug(level, "build123d Axis", obj_name)

        if is_wrapped(cad_obj):
            cad_obj = cad_obj.wrapped
        coord = get_axis_coord(cad_obj)
        name = get_name(cad_obj, obj_name, "Axis")
        ocp_obj = CoordAxis(
            name,
            coord["origin"],
            coord["z_dir"],
            size=helper_scale,
        )
        return ocp_obj

    def to_ocp(
        self,
        *cad_objs,
        names=None,
        colors=None,
        alphas=None,
        loc=None,
        render_mates=None,
        render_joints=None,
        helper_scale=1,
        default_color=None,
        show_parent=False,
        sketch_local=False,
        unroll_compounds=False,
        cache_id=None,
        top_level=True,
        level=0,
    ):
        if loc is None:
            loc = identity_location()
        group = OcpGroup(loc=loc)

        # ============================= Validate parameters ============================= #

        if names is None:
            names = [None] * len(cad_objs)
        else:
            if len(names) != len(cad_objs):
                raise ValueError("Length of names does not match the number of objects")
            names = make_unique(names)

        if alphas is None:
            alphas = [None] * len(cad_objs)

        if len(alphas) != len(cad_objs):
            raise ValueError("Length of alphas does not match the number of objects")

        if colors is None:
            colors = [None] * len(cad_objs)
        else:
            if len(colors) != len(cad_objs):
                raise ValueError(
                    "Length of colors does not match the number of objects"
                )
            colors = [get_rgba(c, a) for c, a in zip(colors, alphas)]

        if default_color is None:
            default_color = get_default("default_color")

        # =========================== Loop over all objects ========================== #

        for cad_obj, obj_name, rgba_color in zip(cad_objs, names, colors):

            # ===================== Silently skip enums and known types ===================== #
            if (
                isinstance(cad_obj, enum.Enum)
                or is_ocp_color(cad_obj)
                or isinstance(cad_obj, (int, float, bool, str, np.number, np.ndarray))
            ):
                continue

            # ===== Filter: Only process CAD objects and print a skipping message else ====== #
            if not (
                is_wrapped(cad_obj)
                or isinstance(cad_obj, (Iterable, dict))
                or is_cadquery(cad_obj)
                or is_cadquery_assembly(cad_obj)
                or is_cadquery_sketch(cad_obj)
                or is_build123d(cad_obj)
                or is_compound(cad_obj)
                or is_topods_shape(cad_obj)
                or is_toploc_location(cad_obj)
            ):
                print(
                    "Skipping object"
                    + ("" if obj_name is None else f" '{obj_name}'")
                    + f" of type {type(cad_obj)}"
                )
                continue

            # ================================= Prepare ================================= #

            # Get object color
            if rgba_color is not None and not isinstance(rgba_color, Color):
                rgba_color = get_rgba(rgba_color)

            elif hasattr(cad_obj, "color") and cad_obj.color is not None:
                rgba_color = get_rgba(cad_obj.color)

            # ================================ Iterables ================================ #

            # Generic iterables (tuple, list, but not ShapeList)
            if isinstance(cad_obj, (list, tuple)) and not is_build123d_shapelist(
                cad_obj
            ):
                ocp_obj = self.handle_list_tuple(
                    cad_obj, obj_name, rgba_color, sketch_local, helper_scale, level
                )

            # Compounds / topods_compounds
            elif (
                is_compound(cad_obj)
                and (is_mixed_compound(cad_obj) or unroll_compounds)
            ) or (
                is_topods_compound(cad_obj)
                and (is_mixed_compound(cad_obj) or unroll_compounds)
            ):
                ocp_obj = self.handle_compound(
                    cad_obj, obj_name, rgba_color, sketch_local, helper_scale, level
                )

            # Dicts
            elif isinstance(cad_obj, dict):
                ocp_obj = self.handle_dict(
                    cad_obj, obj_name, rgba_color, sketch_local, helper_scale, level
                )

            # =============================== Assemblies ================================ #

            elif is_build123d_assembly(cad_obj):
                ocp_obj = self.handle_build123d_assembly(
                    cad_obj, obj_name, rgba_color, sketch_local, level
                )
                ocp_obj = self.handle_build123d_assembly(
                    cad_obj, obj_name, rgba_color, helper_scale, sketch_local, level
                )

            # =============================== Conversions =============================== #

            # build123d ShapeList
            elif is_build123d_shapelist(cad_obj) or (
                is_cadquery(cad_obj) and not is_cadquery_empty_workplane(cad_obj)
            ):
                ocp_obj = self.handle_shapelist(
                    cad_obj, obj_name, rgba_color, sketch_local, level
                )

            # build123d BuildPart, BuildSketch, BuildLine
            elif is_build123d(cad_obj):
                ocp_obj = self.handle_build123d_builder(
                    cad_obj, obj_name, rgba_color, sketch_local, level
                )

            # TopoDS_Shape, TopoDS_Compound, TopoDS_Edge, TopoDS_Face, TopoDS_Shell,
            # TopoDS_Solid, TopoDS_Vertex, TopoDS_Wire,
            # build123d Shape, Compound, Edge, Face, Shell, Solid, Vertex
            # CadQuery shapes Solid, Shell, Face, Wire, Edge, Vertex
            elif (
                is_topods_shape(cad_obj)
                or is_build123d_shape(cad_obj)
                or is_cadquery_shape(cad_obj)
            ):
                ocp_obj = self.handle_shapes(
                    cad_obj, obj_name, rgba_color, sketch_local, level
                )

            # Cadquery sketches
            elif is_cadquery_sketch(cad_obj):
                ocp_obj = self.handle_cadquery_sketch(
                    cad_obj, obj_name, rgba_color, level
                )

            # build123d Location/Plane or TopLoc_Location or gp_Pln
            elif (
                is_build123d_location(cad_obj)
                or is_toploc_location(cad_obj)
                or is_build123d_plane(cad_obj)
                or is_gp_plane(cad_obj)
                or is_cadquery_empty_workplane(cad_obj)
            ):
                ocp_obj = self.handle_locations_planes(
                    cad_obj, obj_name, rgba_color, helper_scale, sketch_local, level
                )

            # build123d Axis or gp_Ax1
            elif is_build123d_axis(cad_obj) or is_gp_axis(cad_obj):
                ocp_obj = self.handle_axis(
                    cad_obj, obj_name, rgba_color, helper_scale, sketch_local, level
                )

            else:
                raise ValueError(f"Unknown object type: {cad_obj}")

            if DEBUG:
                print(f"{'  '*level}=>", ocp_obj)

            group.add(ocp_obj)

        group.make_unique_names()

        if group.length == 1 and isinstance(group.objects[0], OcpGroup):
            group = group.cleanup()

        return group


class Progress:
    def update(self, mark):
        print(mark, end="", flush=True)


def to_assembly(
    *cad_objs,
    names=None,
    colors=None,
    alphas=None,
    render_mates=None,
    render_joints=None,
    helper_scale=1,
    default_color=None,
    show_parent=False,
    show_sketch_local=True,
    loc=None,
    mates=None,
    progress=None,
):
    converter = OcpConverter(progress=Progress())
    ocp_group = converter.to_ocp(
        *cad_objs,
        names=names,
        colors=colors,
        alphas=alphas,
        loc=loc,
        render_mates=render_mates,
        render_joints=render_joints,
        helper_scale=helper_scale,
        default_color=default_color,
        show_parent=show_parent,
        sketch_local=show_sketch_local,
    )
    instances = [{"obj": i[0], "cache_id": i[1]} for i in converter.instances]

    return ocp_group, instances


def tessellate_group(group, instances, kwargs=None, progress=None, timeit=False):
    overall_bb = BoundingBox()

    def _add_bb(shapes):
        for shape in shapes["parts"]:
            if shape.get("parts") is None:
                if shape["type"] == "shapes":
                    ind = shape["shape"]["ref"]
                    with Timer(
                        timeit,
                        f"instance({ind})",
                        "create bounding boxes:     ",
                        2,
                    ) as t:
                        shape["bb"] = np_bbox(
                            meshed_instances[ind]["vertices"],
                            *shape["loc"],
                        )
                        overall_bb.update(shape["bb"])

            else:
                _add_bb(shape)

    def _discretize_edges(obj, name, id_):
        with Timer(timeit, name, "bounding box:", 2) as t:
            deviation = preset("deviation", kwargs.get("deviation"))
            edge_accuracy = preset("edge_accuracy", kwargs.get("edge_accuracy"))

            bb = bounding_box(obj)
            quality = compute_quality(bb, deviation=deviation)
            deflection = quality / 100 if edge_accuracy is None else edge_accuracy
            t.info = str(bb)

        with Timer(timeit, name, "discretize:  ", 2) as t:
            t.info = f"quality: {quality}, deflection: {deflection}"
            disc_edges = discretize_edges(obj, deflection, id_)

        return disc_edges, bb

    def _convert_vertices(obj, _name, id_):
        bb = bounding_box(obj)
        vertices = convert_vertices(obj, id_)

        return vertices, bb

    if kwargs is None:
        kwargs = {}

    mapping, shapes = group.collect(
        "", instances, None, _discretize_edges, _convert_vertices
    )

    states = group.to_state()

    meshed_instances = []

    deviation = preset("deviation", kwargs.get("deviation"))
    angular_tolerance = preset("angular_tolerance", kwargs.get("angular_tolerance"))

    render_edges = preset("render_edges", kwargs.get("render_edges"))

    for i, instance in enumerate(instances):
        with Timer(timeit, f"instance({i})", "compute quality:", 2) as t:
            shape = instance["obj"]
            # A first rough estimate of the bounding box.
            # Will be too large, but is sufficient for computing the quality
            # location is not relevant here
            bb = bounding_box(shape, loc=None, optimal=False)
            quality = compute_quality(bb, deviation=deviation)
            t.info = str(bb)

        with Timer(timeit, f"instance({i})", "tessellate:     ", 2) as t:
            mesh = tessellate(
                shape,
                instance["cache_id"],
                deviation=deviation,
                quality=quality,
                angular_tolerance=angular_tolerance,
                debug=timeit,
                compute_edges=render_edges,
                progress=progress,
                shape_id="n/a",
            )
            meshed_instances.append(mesh)
            t.info = (
                f"{{quality:{quality:.4f}, angular_tolerance:{angular_tolerance:.2f}}}"
            )
    _add_bb(shapes)
    # print("overall_bb =", overall_bb.to_dict())
    # shapes["bb"] = bb

    # print(bb)

    return meshed_instances, shapes, states, mapping


#
# Interface functions
#


def combined_bb(shapes):
    def c_bb(shapes, bb):
        for shape in shapes["parts"]:
            if shape.get("parts") is None:
                if bb is None:
                    if shape["bb"] is None:
                        bb = BoundingBox()
                    else:
                        bb = BoundingBox(shape["bb"])
                else:
                    if shape["bb"] is not None:
                        bb.update(shape["bb"])

                # after updating the global bounding box, remove the local
                del shape["bb"]
            else:
                bb = c_bb(shape, bb)
        return bb

    bb = c_bb(shapes, None)
    return bb


def conv():
    raise NotImplementedError("conv is not implemented any more")
