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

Revision History
--------
2016-03-5: Modified to allow for a list of ModelClasses as input - Brian Cherinka
2016-03-11: Modified to output a dictionary of parameters: values - B. Cherinka
2016-03-16: Changed sqlalchemy values in conditions to bindparam for post-replacement - B. Cherinka
2016-03-24: Allowed for = to mean equality for non string fields and LIKE for strings - B. Cherinka
          : Changed the dot relationship in get_field to filter on the relationship_name first - B. Cherinka
2016-05-11: Added support for PostgreSQL array filters, using value op ANY(array) - B. Cherinka
2016-09-12: Modified to allow for function names (with nested expression) in the expression - B. Cherinka
2016-09-12: Modified to separate out function conditions from regular conditions
2016-09-21: Modified field checks to allow for hybrid properties to pass through - B. Cherinka
2016-09-24: Added in Decimal field as an fieldtype option - B. Cherinka
2016-09-29: Modified the bindparam name to allow for value ranges of a single name - B. Cherinka
"""

from __future__ import print_function
import pyparsing as pp
import inspect
import decimal
from pyparsing import ParseException  # explicit export
from sqlalchemy import func, bindparam
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import or_, and_, not_, sqltypes
from operator import le, ge, gt, lt, eq, ne

opdict = {'<=': le, '>=': ge, '>': gt, '<': lt, '!=': ne, '==': eq, '=': eq}


# Define a custom exception class
class BooleanSearchException(Exception):
    pass


# ***** Utility functions *****
def get_field(DataModelClass, field_name, base_name=None):
    """ Returns a SQLAlchemy Field from a field name such as 'name' or 'parent.name'.
        Returns None if no field exists by that field name.
    """
    # Handle hierarchical field names such as 'parent.name'
    if base_name:
        if base_name in DataModelClass.__tablename__:
            return getattr(DataModelClass, field_name, None)
        else:
            return None

    # Handle flat field names such as 'name'
    return getattr(DataModelClass, field_name, None)


# ***** Define the expression element classes *****

class FxnCondition(object):
    ''' Represents a fxn-operand-value condition where
    the fxn is a function containing a new condition
    '''
    def __init__(self, data):
        self.fxn = data[0][0]
        self.fxnname = self.fxn[0]
        self.fxncond = self.fxn[1]
        self.op = data[0][1]
        self.value = data[0][2]

    def filter(self, DataModelClass):
        return None

    def __repr__(self):
        return '{0}({1})'.format(self.fxnname, self.fxncond) + self.op + self.value


class Condition(object):
    """ Represents a 'name operand value' condition,
        where operand can be one of: '<', '<=', '=', '==', '!=', '>=', '>'.
    """
    def __init__(self, data):
        self.fullname = data[0][0]
        if '.' in self.fullname:
            self.basename, self.name = self.fullname.split('.', 1)
        else:
            self.basename = None
            self.name = self.fullname
        self.op = data[0][1]
        self.value = data[0][2]
        uniqueparams.append(self.fullname)
        if self.fullname not in params:
            params.update({self.fullname: self.value})
            self.bindname = self.fullname
        else:
            count = params.keys().count(self.fullname)
            self.bindname = '{0}_{1}'.format(self.fullname, count)
            params.update({self.fullname: self.value})

    def filter(self, DataModelClass):
        ''' Return the condition as an SQLalchemy query condition '''

        condition = None
        if inspect.ismodule(DataModelClass):
            # one module
            models = [i[1] for i in inspect.getmembers(DataModelClass, inspect.isclass) if hasattr(i[1], '__tablename__')]
        else:
            # list of Model Classes
            if isinstance(DataModelClass, list):
                models = DataModelClass
            else:
                models = None

        if models:
            # Input is a a list of DataModelClasses
            field = None
            index = None
            for i, model in enumerate(models):

                field = get_field(model, self.name, base_name=self.basename)
                try:
                    ptype = field.type
                    ilike = field.ilike
                except AttributeError as e:
                    ptype = None
                    ilike = None

                if not isinstance(field, type(None)) and ptype and ilike:
                    index = i
                    break

            if isinstance(field, type(None)):
                raise BooleanSearchException(
                    "Table '%(table_name)s' does not have a field named '%(field_name)s'."
                    % dict(table_name=model.__tablename__, field_name=self.name))

            condition = self.filter_one(models[index], field=field, condition=condition)

        else:
            # Input is only one DataModelClass
            field = get_field(DataModelClass, self.name)
            if field:
                condition = self.filter_one(DataModelClass, field=field, condition=condition)
            else:
                raise BooleanSearchException(
                    "Table '%(table_name)s' does not have a field named '%(field_name)s'."
                    % dict(table_name=DataModelClass.__tablename__, field_name=self.name))

        return condition

    def bindAndLowerValue(self, field):
        '''Bind and lower the value based on field type '''

        # get python field type
        ftypes = [float, int, decimal.Decimal]
        fieldtype = field.type.python_type
        if fieldtype == float or fieldtype == decimal.Decimal:
            try:
                value = float(self.value)
                lower_field = field
            except:
                raise BooleanSearchException(
                    "Field {0} expects a float value. Received value {1} instead.".format(self.name, self.value))
        elif fieldtype == int:
            try:
                value = int(self.value)
                lower_field = field
            except:
                raise BooleanSearchException(
                    "Field {0} expects an integer value. Received value {1} instead.".format(self.name, self.value))
        else:
            lower_field = func.lower(field)
            value = self.value

        # Bind the parameter value to the parameter name
        boundvalue = bindparam(self.bindname, value)
        lower_value = func.lower(boundvalue) if fieldtype not in ftypes else boundvalue

        return lower_field, lower_value

    def filter_one(self, DataModelClass, field=None, condition=None):
        """ Return the condition as a SQLAlchemy query condition
        """
        if not isinstance(field, type(None)):
            # Prepare field and value
            lower_field, lower_value = self.bindAndLowerValue(field)

            # Handle Arrays
            if isinstance(field.type, postgresql.ARRAY):
                condition = field.any(self.value, operator=opdict[self.op])
            else:
                # Do Normal Scalar Stuff

                # Return SQLAlchemy condition based on operator value
                # self.name is parameter name, lower_field is Table.parameterName
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
                    if isinstance(field.type, sqltypes.TEXT) or \
                       isinstance(field.type, sqltypes.VARCHAR) or \
                       isinstance(field.type, sqltypes.String):
                        # this operator maps to LIKE
                        # x=5 -> x LIKE '%5%' (x contains 5)
                        # x=5* -> x LIKE '5%' (x starts with 5)
                        # x=*5 -> x LIKE '%5' (x ends with 5)
                        field = getattr(DataModelClass, self.name)
                        value = self.value
                        if value.find('*') >= 0:
                            value = value.replace('*', '%')
                            condition = field.ilike(bindparam(self.bindname, value))
                        else:
                            condition = field.ilike('%' + bindparam(self.bindname, value) + '%')
                    else:
                        # if not a text column, then use "=" as a straight equals
                        condition = lower_field.__eq__(boundvalue)

        return condition

    def __repr__(self):
        return self.fullname + self.op + self.value


class BoolNot(object):
    """ Represents the boolean operator NOT
    """
    def __init__(self, data):
        self.condition = data[0][1]
        if isinstance(self.condition, Condition) and self.condition.name not in params:
            params.update({self.condition.fullname: self.condition.value})

    def filter(self, DataModelClass):
        """ Return the operator as a SQLAlchemy not_() condition
        """
        if not isinstance(self.condition, FxnCondition):
            return not_(self.condition.filter(DataModelClass))

    def __repr__(self):
        return 'not_(' + repr(self.condition) + ')'


class BoolAnd(object):
    """ Represents the boolean operator AND
    """
    def __init__(self, data):
        self.conditions = []
        for condition in data[0]:
            if condition and condition != 'and':
                if isinstance(condition, FxnCondition):
                    functions.append(condition)
                else:
                    self.conditions.append(condition)
                if isinstance(condition, Condition) and condition.name not in params:
                    params.update({condition.fullname: condition.value})

    def filter(self, DataModelClass):
        """ Return the operator as a SQLAlchemy and_() condition
        """
        conditions = [condition.filter(DataModelClass) for condition in self.conditions if not isinstance(condition, FxnCondition)]
        return and_(*conditions)  # * converts list to argument sequence

    def removeFunctions(self):
        ''' remove the fxn conditions '''
        self.conditions = [condition for condition in self.conditions if not isinstance(condition, FxnCondition)]

    def __repr__(self):
        return 'and_(' + ', '.join([repr(condition) for condition in self.conditions]) + ')'


class BoolOr(object):
    """ Represents the boolean operator OR
    """
    def __init__(self, data):
        self.conditions = []
        for condition in data[0]:
            if condition and condition != 'or':
                if isinstance(condition, FxnCondition):
                    functions.append(condition)
                else:
                    self.conditions.append(condition)
                if isinstance(condition, Condition) and condition.name not in params:
                    params.update({condition.fullname: condition.value})

    def filter(self, DataModelClass):
        """ Return the operator as a SQLAlchemy or_() condition
        """
        conditions = [condition.filter(DataModelClass) for condition in self.conditions if not isinstance(condition, FxnCondition)]
        return or_(*conditions)  # * converts list to argument sequence

    def __repr__(self):
        return 'or_(' + ', '.join([repr(condition) for condition in self.conditions]) + ')'

    def removeFunctions(self):
        ''' remove the fxn conditions '''
        self.conditions = [condition for condition in self.conditions if not isinstance(condition, FxnCondition)]

# ***** Define the boolean condition expressions *****

# Define expression elements
LPAR = pp.Suppress('(')
RPAR = pp.Suppress(')')
number = pp.Regex(r"[+-]?\d+(:?\.\d*)?(:?[eE][+-]?\d+)?")
name = pp.Word(pp.alphas + '._', pp.alphanums + '._')
operator = pp.Regex("==|!=|<=|>=|<|>|=")
value = pp.Word(pp.alphanums + '-_.*') | pp.QuotedString('"') | number

whereexp = pp.Forward()
# condition
condition = pp.Group(name + operator + value)
condition.setParseAction(Condition)
# fxn condition
function_call = pp.Group(pp.Word(pp.alphas) + LPAR + condition + RPAR)
fxn_cond = pp.Group(function_call + operator + value)
fxn_cond.setParseAction(FxnCondition)
# combine
wherecond = condition | fxn_cond
whereexp <<= wherecond

# Define the expression as a hierarchy of boolean operators
# with the following precedence: NOT > AND > OR
expression_parser = pp.operatorPrecedence(whereexp, [
    (pp.CaselessLiteral("not"), 1, pp.opAssoc.RIGHT, BoolNot),
    (pp.CaselessLiteral("and"), 2, pp.opAssoc.LEFT, BoolAnd),
    (pp.CaselessLiteral("or"), 2, pp.opAssoc.LEFT, BoolOr),
])

params = {}
uniqueparams = []
functions = []


def parse_boolean_search(boolean_search):
    """ Parses the boolean search expression into a hierarchy of boolean operators.
        Returns a BoolNot or BoolAnd or BoolOr object.
    """
    global params, functions, uniqueparams
    params = {}
    uniqueparams = []
    functions = []
    try:
        expression = expression_parser.parseString(boolean_search)[0]
    except ParseException as e:
        raise BooleanSearchException("Syntax error at offset %(offset)s." % dict(offset=e.col))
    else:
        expression.params = params
        expression.uniqueparams = list(set(uniqueparams))
        expression.functions = functions
        return expression


