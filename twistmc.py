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
