# Copyright 2015 SolidBuilds.com. All rights reserved.
#
# Authors: Ling Thio <ling.thio@gmail.com>

"""
SQLAlchemy-boolean-search
=========================
SQLAlchemy-boolean-search translates a boolean search expression such as::

    field1=*something* and not (field2==1 or field3<=10.0)

into its corresponding SQLAlchemy query filter.

Install
-------

    pip install sqlalchemy-boolean-search

Usage example
-------------

    from sqlalchemy_boolean_search import parse_boolean_search

    # DataModel defined elsewhere (with field1, field2 and field3)
    from app.models import DataModel

    # Parse boolean search into a parsed expression
    boolean_search = 'field1=*something* and not (field2==1 or field3<=10.0)'
    parsed_expression = parse_boolean_search(boolean_search)

    # Retrieve records using a filter generated by the parsed expression
    records = DataModel.query.filter(parsed_expression.filter(DataModel))

Documentation
-------------
http://sqlalchemy-boolean-search.readthedocs.org/

Authors
-------
* Ling Thio - ling.thio [at] gmail.com
"""

from __future__ import print_function
import pyparsing as pp
from pyparsing import ParseException  # explicit export
from sqlalchemy import func
from sqlalchemy.sql import or_, and_, not_, sqltypes

# Define a custom exception class
class BooleanSearchException(Exception):
    pass

# ***** Utility functions *****
def get_field(DataModelClass, field_name):
    """ Returns a SQLAlchemy Field from a field name such as 'name' or 'parent.name'.
        Returns None if no field exists by that field name.
    """
    # Handle hierarchical field names such as 'parent.name'
    if '.' in field_name:
        relationship_name, field_name = field_name.split('.', 1)
        relationship = getattr(DataModelClass, relationship_name)
        return get_field(relationship.property.mapper.entity, field_name)

    # Handle flat field names such as 'name'
    return getattr(DataModelClass, field_name, None)

# ***** Define the expression element classes *****

class Condition(object):
    """ Represents a 'name operand value' condition,
        where operand can be one of: '<', '<=', '=', '==', '!=', '>=', '>'.
    """
    def __init__(self, data):
        self.name = data[0][0]
        self.op = data[0][1]
        self.value = data[0][2]

    def filter(self, DataModelClass):
        """ Return the condition as a SQLAlchemy query condition
        """
        condition = None
        field = get_field(DataModelClass, self.name)
        if field:
            # Prepare field and value
            lower_field = func.lower(field)
            value = self.value
            lower_value = func.lower(value)
            if field.type.python_type == float:
                try:
                    value = float(value)
                    lower_field = field
                    lower_value = value
                except:
                    raise BooleanSearchException(
                        "Field '%(name)s' expects a float value. Received value '%(value)s' instead."
                        % dict(name=self.name, value=self.value))
            elif field.type.python_type == int:
                try:
                    value = int(value)
                    lower_field = field
                    lower_value = value
                except:
                    raise BooleanSearchException(
                        "Field '%(name)s' expects an integer value. Received value '%(value)s' instead."
                        % dict(name=self.name, value=self.value))

            # Return SQLAlchemy condition based on operator value
            if self.op == '==':
                condition = lower_field.__eq__(lower_value)
            elif self.op == '<':
                condition = lower_field.__lt__(lower_value)
            elif self.op == '<=':
                condition = lower_field.__le__(lower_value)
            elif self.op == '>':
                condition = lower_field.__gt__(lower_value)
            elif self.op == '>=':
                condition = lower_field.__ge__(lower_value)
            elif self.op == '!=':
                condition = lower_field.__ne__(lower_value)
            elif self.op == '=':
                field = getattr(DataModelClass, self.name)
                value = self.value
                if value.find('*') >= 0:
                    value = value.replace('*', '%')
                    condition = field.ilike(value)
                else:
                    condition = field.ilike('%' + value + '%')
        else:
            raise BooleanSearchException(
                "Table '%(table_name)s' does not have a field named '%(field_name)s'."
                % dict(table_name=DataModelClass.__tablename__, field_name=self.name))

        return condition

    def __repr__(self):
        return self.name + self.op + self.value


class BoolNot(object):
    """ Represents the boolean operator NOT
    """
    def __init__(self, data):
        self.condition = data[0][1]

    def filter(self, DataModelClass):
        """ Return the operator as a SQLAlchemy not_() condition
        """
        return not_(self.condition.filter(DataModelClass))

    def __repr__(self):
        return 'not_(' + repr(self.condition) + ')'


class BoolAnd(object):
    """ Represents the boolean operator AND
    """
    def __init__(self, data):
        self.conditions = [condition for condition in data[0] if condition and condition != 'and']

    def filter(self, DataModelClass):
        """ Return the operator as a SQLAlchemy and_() condition
        """
        conditions = [condition.filter(DataModelClass) for condition in self.conditions]
        return and_(*conditions)  # * converts list to argument sequence

    def __repr__(self):
        return 'and_(' + ', '.join([repr(condition) for condition in self.conditions]) + ')'


class BoolOr(object):
    """ Represents the boolean operator OR
    """
    def __init__(self, data):
        self.conditions = [condition for condition in data[0] if condition and condition != 'or']

    def filter(self, DataModelClass):
        """ Return the operator as a SQLAlchemy or_() condition
        """
        conditions = [condition.filter(DataModelClass) for condition in self.conditions]
        return or_(*conditions)  # * converts list to argument sequence

    def __repr__(self):
        return 'or_(' + ', '.join([repr(condition) for condition in self.conditions]) + ')'

# ***** Define the boolean condition expressions *****

# Define expression elements
number = pp.Regex(r"[+-]?\d+(:?\.\d*)?(:?[eE][+-]?\d+)?")
name = pp.Word(pp.alphas + '._', pp.alphanums + '._')
operator = pp.Regex("==|!=|<=|>=|<|>|=")
value = pp.Word(pp.alphanums + '_.*') | pp.QuotedString('"') | number
condition = pp.Group(name + operator + value)
condition.setParseAction(Condition)

# Define the expression as a hierarchy of boolean operators
# with the following precedence: NOT > AND > OR
expression_parser = pp.operatorPrecedence(condition, [
    (pp.CaselessLiteral("not"), 1, pp.opAssoc.RIGHT, BoolNot),
    (pp.CaselessLiteral("and"), 2, pp.opAssoc.LEFT, BoolAnd),
    (pp.CaselessLiteral("or"), 2, pp.opAssoc.LEFT, BoolOr),
])


def parse_boolean_search(boolean_search):
    """ Parses the boolean search expression into a hierarchy of boolean operators.
        Returns a BoolNot or BoolAnd or BoolOr object.
    """
    try:
        expression = expression_parser.parseString(boolean_search)[0]
        return expression
    except ParseException as e:
        raise BooleanSearchException(
            "Syntax error at offset %(offset)s."
            % dict(offset=e.col))

