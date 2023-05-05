import graphene
from grunnlag.scalars import AssignationID


class RepresentationViewInput(graphene.InputObjectType):
        z_min = graphene.Int(description="The x coord of the position (relative to origin)")
        z_max = graphene.Int(description="The x coord of the position (relative to origin)")
        t_min = graphene.Int(description="The x coord of the position (relative to origin)")
        t_max = graphene.Int(description="The x coord of the position (relative to origin)")
        c_min = graphene.Int(description="The x coord of the position (relative to origin)")
        c_max = graphene.Int(description="The x coord of the position (relative to origin)")
        x_min = graphene.Int(description="The x coord of the position (relative to origin)")
        x_max = graphene.Int(description="The x coord of the position (relative to origin)")
        y_min = graphene.Int(description="The x coord of the position (relative to origin)")
        y_max = graphene.Int(description="The x coord of the position (relative to origin)")
        channel = graphene.ID(description="The channel you want to associate with this map", required=False)
        position = graphene.ID(description="The position you want to associate with this map", required=False)
        timepoint = graphene.ID(description="The position you want to associate with this map", required=False)
        created_while = AssignationID(required=False, description="The assignation id")

class ViewInput(graphene.InputObjectType):
    omero = graphene.ID(
                required=True, description="The stage this position belongs to"
            )
    z_min = graphene.Int(description="The x coord of the position (relative to origin)")
    z_max = graphene.Int(description="The x coord of the position (relative to origin)")
    t_min = graphene.Int(description="The x coord of the position (relative to origin)")
    t_max = graphene.Int(description="The x coord of the position (relative to origin)")
    c_min = graphene.Int(description="The x coord of the position (relative to origin)")
    c_max = graphene.Int(description="The x coord of the position (relative to origin)")
    x_min = graphene.Int(description="The x coord of the position (relative to origin)")
    x_max = graphene.Int(description="The x coord of the position (relative to origin)")
    y_min = graphene.Int(description="The x coord of the position (relative to origin)")
    y_max = graphene.Int(description="The x coord of the position (relative to origin)")
    channel = graphene.ID(description="The channel you want to associate with this map", required=False)
    position = graphene.ID(description="The position you want to associate with this map", required=False)
    timepoint = graphene.ID(description="The position you want to associate with this map", required=False)
    created_while = AssignationID(required=False, description="The assignation id")