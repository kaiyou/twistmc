""" Manages a component-based archiecture with asynchronous initialization
"""

import functools
import pyconsts

ATTRS = pyconsts.text(
    "__twistmc_%s__",
    # These consts are used as attribute names when monkey patching the
    # twistmc objects.
    "factory"   # factory class method for a given class
    "features"  # list of class features
)


class Plugin(object):
    """ Component plugin.

    A plugin is a separate component that exposes the required set of features.
    Plugins will be resolved a injection time.
    """
    pass


class Collection(object):
    """ Collection of plugins.

    A collection is a set of separate components that expose the required set
    of features. Unlike plugins, collections are updated at runtime when a
    new component is loaded or unloaded.
    """
    pass


def component(*features):
    """ Decorates a component class.

    Every class that exposes component features should be decorated. Features
    are passed as parameters when decorating the class. For instance :

        @twistmc.component("resource_manager")
        class DummyResourceManager(object):
            pass

    A single class may expose multiple features. If you cannot decorate a
    given class, see ``declare_component``.
    """
    def decorator(cls):
        setattr(cls, ATTRS.features, features)
        return cls

    return decorator


def plugin(*features):
    """ Embeds a plugin in a component.
    """
    return Plugin()


def collection(*features):
    """ Embeds a collection in a component.
    """
    return Collection()


def factory(func):
    """ Decorates a class factory.

    Every class factory is a class method that must return a class instance
    or None or raise an exception. The factory is then stored as a class
    attribute for later use when injecting dependencies.
    """
    @functools.wraps(func)
    def wrapper(cls, *args, **kwargs):
        result = func(cls, *args, **kwargs)
        if type(result) is not cls:
            raise TypeError("Factory type mismatch")
        elif result is None:
            raise ValueError("Factory failed to build an instance")
        return result

    setattr(func, ATTRS.factory, True)
    return classmethod(wrapper)
