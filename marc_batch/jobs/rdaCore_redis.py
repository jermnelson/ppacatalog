"""
 :mod:`frbr_redis` Module ingests MARC records into a FRBR-Redis datastore 
 controlled by the MARC Batch app. This Module initially ingests the RDA
 core elements from MARC into datastore.
"""
__author__ = 'Jeremy Nelson'
import pymarc,redis,logging,sys
import re,datetime
from marc_batch.fixures import json_loader
from call_number.redis_helpers import ingest_call_numbers


try:
    import aristotle.settings as settings
    REDIS_HOST = settings.REDIS_ACCESS_HOST
    REDIS_PORT = settings.REDIS_ACCESS_PORT
    TEST_DB = settings.REDIS_TEST
    volatile_redis = redis.StrictRedis(host=settings.REDIS_PRODUCTIVITY_HOST,
                                       port=settings.REDIS_PRODUCTIVITY_PORT)
except:
    REDIS_HOST = '127.0.0.1'
    REDIS_PORT = 6379
    REDIS_TEST_DB = 3

# RDA Core should reside on primary DB of 0
redis_server = redis.StrictRedis(host=REDIS_HOST,
                                 port=REDIS_PORT)    

year_re = re.compile(r"(\d+)")



class MARCRules(object):

    def __init__(self,**kwargs):
        self.json_results = {}
        if kwargs.has_key('json_file'):
            json_file = kwargs.get('json_file')
            self.json_rules = json_loader[json_file]
        if kwargs.has_key('json_rules'):
            self.json_rules = kwargs.get('json_rules')

    def __get_position_values__(self,rule,marc_field):
        """
        Helper method checks MARC values from fixed positions based
        on the rule's position
        """
        values = str()
        if rule.has_key("positions") and marc_field.is_control_field():
            start_position = int(rule["positions"]["start"])
            end_position = int(rule["positions"]["end"])
            raw_value = marc_field.value()
            # Adding +1 to end position for string range
            values += raw_value[start_position:end_position+1]
        if len(values) < 1:
            return None
        return values

    def __get_subfields__(self,rule,marc_field):
        """
        Helper method extracts any subfields that match pattern
        in the rule's subfield

        :param rule: JSON Rule
        :param marc_field: MARC field
        """
        output = None
        if rule.has_key("subfields") and hasattr(marc_field,'subfields'):
            if hasattr(rule,"condition"):
                if self.__test_subfield__(rule,marc_field) is False:
                    return output
            rule_subfields = rule["subfields"]
            output = []
            for subfield in rule_subfields:
                output.append(''.join(marc_field.get_subfields(subfield)))
        return output


    def __test_indicators__(self,rule,marc_field):
        """
        Helper method checks the MARC field indicator againest the
        rule values
        
        :param rule: JSON Rule
        :param marc_field: MARC field
        """
        pass_rule = None
        if rule.has_key("indicators"):
            indicator0,indicator1 = marc_field.indicators
            if rule["indicators"].has_key("0"):
                if rule["indicators"]["0"].count(indicator0) > -1:
                    pass_rule = True
            if rule["indicators"].has_key("1"):
                if rule["indicators"]["1"].count(indicator1) > -1:
                    pass_rule = True
            if pass_rule is None:
                pass_rule = False
        return pass_rule

    def __test_position_values__(self,rule,marc_field):
        """
        Helper method checks MARC values from fixed positions based
        on the rule's position
        """
        pass_rule = None
        if rule.has_key("positions") and rule.has_key("condition"):
            raw_value = marc_field.value()
            # NOTE condition should be python lambda form and evaluates
            # to boolean
            condition = eval(rule["condition"])
            pass_rule = condition(raw_value)
        return pass_rule

    def __test_subfield__(self,rule,marc_field):
        """
        Helper function evaluates a lambda function againest value of
        MARC field, returns boolean
        """
        if not rule.has_key("condition"):
            return None
        condition = eval(rule["condition"])
        return condition(marc_field)
        

    def load_marc(self,marc_record):
        """
        Method takes a MARC record and applies all of its rules
        to the MARC file. If a MARC field matches the rule's condition,
        the result is saved to json results dict.

        :param marc_record: MARC record
        """
        for rda_element in self.json_rules.keys():
            rule_fields = self.json_rules[rda_element].keys()
            for tag in rule_fields:
                marc_fields = marc_record.get_fields(tag)
                if len(marc_fields) > 0:
                    rule = self.json_rules[rda_element][tag]
                    # Check if indicators are in rule, check
                    # and apply to MARC field
                    for field in marc_fields:
                        test_indicators = self.__test_indicators__(rule,field)
                        if test_indicators is False:
                            pass
                        # For fixed fields
                        position_values = self.__get_position_values__(rule,field)
                        if position_values is not None:
                            if self.json_results.has_key(rda_element):
                                self.json_results[rda_element].append(position_values)
                            else:
                                self.json_results[rda_element] = [position_values,]
                        # For variable fields
                        subfield_values = self.__get_subfields__(rule,field)
                        if subfield_values is not None:
                            if self.json_results.has_key(rda_element):
                                self.json_results[rda_element].extend(subfield_values)
                            else:
                                self.json_results[rda_element] = subfield_values                    
            

class CreateRDACoreEntityFromMARC(object):
    """RDACoreEntity is the base class for ingesting MARC21 records into RDACore
    FRBR entities stored in Redis.

    This class is meant to be over-ridden by child classes for specific RDACore Entities
    """

    def __init__(self,**kwargs):
        self.marc_record = kwargs.get('record')
        self.redis_server = kwargs.get('redis_server')
        self.root_redis_key = kwargs.get('root_redis_key')
        entity_name = kwargs.get('entity')
        base_entity_key = "rdaCore:{0}".format(entity_name)
        redis_incr_value = self.redis_server.incr("global:{0}".format(base_entity_key))
        if kwargs.has_key("json_file"):
            self.marc_rules = MARCRules(json_file=kwargs.get('json_file'))
        elif kwargs.has_key("json_rules"):
            self.marc_rules = MARCRules(json_rules=kwargs.get('json_rules'))
        else:
            raise ValueError("CreateRDACoreEntityFromMARC requires json_file or json_rules")
        # Redis Key for this Entity
        self.entity_key = "{0}:{1}".format(base_entity_key,
                                           redis_incr_value)
    
     
    def generate(self):
        """
        Method iterates through the results of applying the MARC ruleset
        to a MARC record and then creates a hash value for the RDA Core
        entity. The hash value can either be a text string or a key to
        a Redis set of values for the RDA Core entity instance.
        """
        self.marc_rules.load_marc(self.marc_record)
        for element,values in self.marc_rules.json_results.iteritems():
            # Checks to see if rdaCore Entity element already exists
            # in Redis Datastore
            existing_value = self.redis_server.hget(self.entity_key,
                                                    element)
            # rdaCore element doesn't exist for entity. Either add to datastore
            # as a hash value for the rdaCore Entity's element or adds
            # the rdaCore Element as a set to Redis and saves resulting key
            # to the Entity's hash.
            if existing_value is None:
                if len(values) == 1:
                    if len(values[0]) > 0:
                        self.redis_server.hset(self.entity_key,
                                               element,
                                               values[0])
                else:
                    new_set_key = "{0}:{1}".format(self.entity_key,
                                                   element)
                    for row in values:
                        if len(row) > 0:
                            self.redis_server.sadd(new_set_key,
                                                   row)
                    self.redis_server.hset(self.entity_key,
                                           element,
                                           new_set_key)
            # Checks Redis datastore if existing value is a Redis
            # key, if so, checks to make sure it is a set before
            # adding. 
            elif self.redis_server.exists(existing_value):
                if self.redis_server.type(existing_value) == 'set':
                    for row in values:
                        if len(value) > 0:
                            self.redis_server.sadd(existing_value,
                                                   value)
            # By this point, existing_value should be string value
            # extracted from Redis datastore. Checks if the existing
            # value from Redis and the value from the MARC
            # record are the same, adds to existing set if not
            elif values.count(existing_value) < 0:
                set_key = self.redis_server.hget(entity_key,
                                                 element)
                self.redis_server.sadd(set_key,
                                       existing_value)
                for row in values:
                    if len(value) > 0:
                        self.redis_server.sadd(set_key,
                                               value)
                self.redis_server.hset(self.entity_key,
                                       element,
                                       set_key)
            else:
                raise ValueError("{0}:{1} unknown in Redis datastore".format(element,value))            

class CreateRDACoreExpressionFromMARC(CreateRDACoreEntityFromMARC):

    def __init__(self,**kwargs):
        kwargs["entity"] = "Expression"
        kwargs["json_file"] = 'marc-rda-expression'
        super(CreateRDACoreExpressionFromMARC,self).__init__(**kwargs)

    def generate(self):
        super(CreateRDACoreExpressionFromMARC,self).generate()
        self.__call_number_app__()

    def __call_number_app__(self):
        """
        Helper function populates Redis Datastore to support
        specific requirements for the Call Number App including
        a call number hash key - value is this class Redis key
        and a sorted set for LCCN call number.
        """
        ingest_call_numbers(self.marc_record,
                            self.redis_server,
                            self.entity_key)
                

class CreateRDACoreItemFromMARC(CreateRDACoreEntityFromMARC):

    def __init__(self,**kwargs):
        kwargs["entity"] = "Item"
        kwargs["json_file"] = 'marc-rda-item'
        super(CreateRDACoreItemFromMARC,self).__init__(**kwargs)

class CreateRDACoreManifestationFromMARC(CreateRDACoreEntityFromMARC):

    def __init__(self,**kwargs):
        kwargs["entity"] = "Manifestation"
        kwargs["json_file"] = "marc-rda-manifestation"
        super(CreateRDACoreManifestationFromMARC,self).__init__(**kwargs)

    def generate(self):
        # First calls parent generate function
        super(CreateRDACoreManifestationFromMARC,self).generate()
        self.__carrier_type__()
        self.__identifiers__()

    def __call_number_app__(self):
        """
        Helper function populates Redis Datastore to support
        specific requirements for the Call Number App including
        a call number hash key - value is this class Redis key
        and a sorted set for SuDocs and local call number.
        """
        ingest_call_numbers(self.marc_record,
                            self.redis_server,
                            self.entity_key)

    def __carrier_type__(self):
        """
        Secondary lookup for convert MARC character codes into
        a more human-readable form
        """
        carrier_value = self.redis_server.hget(self.entity_key,
                                               'rdaCarrierType')
        # Load json dict mapping MARC 007 position 0 and 1 to RDA Carrier Types
        carrier_types_dict = json_loader.get('marc-carrier-types')        
        # Carrier value is a set and not a single value,
        #!! This is where we should create separate Manifestations and/or
        #!! Expressions for each carrier type.
        if self.redis_server.exists(carrier_value):
            for value in self.redis_server.smembers(carrier_value):
                if len(value) != 2:
                    raise ValueError("Carrier Type codes should only be two chars instead of {0}".format(len(value)))
                position0,position1 = value[0],value[1]
                if carrier_types_dict.has_key(position0):
                    if carrier_types_dict[position0].has_key(position1):
                        # Remove old value and add human friendly value
                        self.redis_server.srem(carrier_value,value)
                        self.redis_server.sadd(carrier_value,
                                               carrier_types_dict[position0][position1])
        elif carrier_value is not None:
            position0,position1 = carrier_value[0],carrier_value[1]
            if carrier_types_dict.has_key(position0):
                    if carrier_types_dict[position0].has_key(position1):
                        self.redis_server.hset(self.entity_key,
                                               'rdaCarrierType',
                                               carrier_types_dict[position0][position1])
    
                    

    def __identifiers__(self):
        """
        Extracts and sets Manifestation's identifiers from MARC record
        """
        identifiers_dict = json_loader.get('manifestation-identifiers')
        identifiers_key = "{0}:identifiers".format(self.entity_key)
        for tag in identifiers_dict.keys():
            marc_fields = self.marc_record.get_fields(tag)
            for field in marc_fields:
                rule = identifiers_dict[tag]
                if rule.has_key('indicators'):
                    marc_indicators = field.indicators
                    # Test position 0 if in rule
                    if rule['indicators'].has_key("0") and len(marc_indicators[0]) > 0:
                        # Test value for indicators in position 0
                        if rule['indicators']['0'].has_key(marc_indicators[0]):
                            rule_subfields = rule['indicators']['0'][marc_indicators[0]]['subfields']
                            rule_label =  rule['indicators']['0'][marc_indicators[0]]['label']
                            # Only one value for identifier, set as string value for
                            # the rule's label in the identifiers hash
                            if len(rule_subfields) == 1:
                                self.redis_server.hset(identifiers_key,
                                                       rule_label,
                                                       ''.join(field.get_subfields(rule_subfields[0])))
                            # Create a string value or set to associate values with the rule's label
                            # in the identifiers hash
                            else:
                                marc_data = []
                                for subfield in rule_subfields:
                                    marc_data.extend(field.get_subfields(subfield))
                                if len(marc_data) == 1:
                                    self.redis_server.hset(identifiers_key,
                                                           rule_label,
                                                           ''.join(marc_data))
                                else:
                                    ident_set_key = "{0}:{1}s".format(self.entity_key,
                                                                      rule['label'].replace(" ",""))
                                    for row in marc_data:
                                        self.redis_server.sadd(ident_set_key,
                                                               row)
                                    self.redis_server.hset(identifiers_key,
                                                           rule_label,
                                                           ident_set_key)
                else:
                    self.redis_server.hset(identifiers_key,
                                           rule['label'],
                                           ''.join(field.get_subfields(rule['subfields'][0])))
                                                                      
                                                                     
                                
                                                   
class CreateRDACorePersonFromMARC(object):

    def __init__(self,**kwargs):
        if kwargs.has_key('json-rules'):
            self.json_rules = kwargs.get('json-rules')
        else:
            self.json_rules = json_loader['marc-rda-person']

    def generate(self):
        super(CreateRDACorePersonFromMARC,self).generate()
        for rda_element in self.json_rules.keys():
            marc_fields = rda_element.keys()
            for field in marc_fields:
                if field.has_key("indicators"):
                    for indicator in field["indicators"]:
                        pass
                    

class CreateRDACoreWorkFromMARC(CreateRDACoreEntityFromMARC):

    def __init__(self,**kwargs):
        kwargs["entity"] = "Work"
        kwargs["json_file"] = 'marc-rda-work'
        super(CreateRDACoreWorkFromMARC,self).__init__(**kwargs)

    def generate(self):
        super(CreateRDACoreWorkFromMARC,self).generate()

        

def create_rda_redis(marc_record,datastore):
    """
    Function takes a MARC record and Redis datastore and
    generates a complete rdaCore Work, Expression, Manifestation,
    and Items

    :param marc_record: MARC record
    :param datastore: Redis datastore
    """
    sys.stderr.write(".")
    # Generate a new rdaCore record key
    root_key = "rdaCore:{0}".format(datastore.incr("global rdaCore"))
    work_creator = CreateRDACoreWorkFromMARC(record=marc_record,
                                             redis_server=datastore,
                                             root_redis_key=root_key)
    work_creator.generate()
    datastore.sadd("rdaCore:Works",work_creator.entity_key)
    expression_creator = CreateRDACoreExpressionFromMARC(record=marc_record,
                                                         redis_server=datastore,
                                                         root_redis_key=root_key)
    expression_creator.generate()
    datastore.sadd("rdaCore:Expressions",expression_creator.entity_key)
    manifestation_creator = CreateRDACoreManifestationFromMARC(record=marc_record,
                                                               redis_server=datastore,
                                                               root_redis_key=root_key)
    manifestation_creator.generate()
    datastore.sadd("rdaCore:Manifestations",manifestation.entity_key)
    item_creator = CreateRDACoreItemFromMARC(record=marc_record,
                                             redis_server=datastore,
                                             root_redis_key=root_key)
    item_creator.generate()
    datastore.sadd("rdaCore:Items",item_creator.entity_key)
                              
    
    
def quick_rda(marc_record,datastore):
    """
    Create a quick-and-dirty rdaCore Redis representation of a MARC
    record

    :param marc_record: MARC record
    :param datastore: Redis datastore
    """
    root_key = "rda:{0}".format(datastore.incr("global rdaCore"))
    bib_number = marc_record['907']['a'][1:-1]
    datastore.hset(root_key,"tutt:bib_number",bib_number)
    work_key = "rda:Works:{0}".format(datastore.incr("global rda:Works"))
    datastore.hset(work_key,"record_key",root_key)
    title_key = "rda:Titles:{0}".format(datastore.incr("global rda:Titles"))
    datastore.hset(title_key,'preferredTitle',marc_record.title())
    datastore.hset(work_key,"titleOfWork",title_key)
    expression_key = "rda:Expressions:{0}".format(datastore.incr("global rda:Expressions"))
    datastore.hset(expression_key,"record_key",root_key)
    datastore.hset("rda:ExpressionOfWork",
                   expression_key,
                   work_key)
    datastore.hset("rda:WorkExpressed",
                   work_key,
                   expression_key)
    manifestation_key = "rda:Manifestations:{0}".format(datastore.incr("global rda:Manifestations"))
    datastore.hset(manifestation_key,"record_key",root_key)
    datastore.hset("rda:ManifestationOfWork",                   
                   manifestation_key,
                   work_key)
    datastore.hset("rda:ManifestationOfExpression",
                   manifestation_key,
                   expression_key)
    datastore.hset("rda:ExpressionManifested",
                   expression_key,
                   manifestation_key)
    datastore.hset("rda:WorkManifested",
                   work_key,
                   manifestation_key)
    field008 = marc_record['008']
    if field008 is not None:
        raw_year = field008.value()[7:11]
        datastore.hset(manifestation_key,"copyrightDate",raw_year)
        year_search = year_re.search(raw_year)
        if year_search is not None:
            year = year_search.groups()[0]
            datastore.zadd("rda:SortedCopyrightDates",int(year),manifestation_key)
            
    item_key = "rda:Items:{0}".format(datastore.incr('global rda:Items'))
    datastore.hset(item_key,"record_key",root_key)
    datastore.hset("rda:ExemplarOfManifestation",
                   item_key,
                   manifestation_key)
    datastore.hset("rda:ManifestationExemplified",
                   manifestation_key,
                   item_key)
    
                                            
    
def process_identifier(marc_record,
                       redis_server,
                       field_tag,
                       identifiers_set_key,
                       subfields_list,
                       ident_root):
    """
    Helper function extracts values from the field's subfields, checks to
    see if the subfield value is in the ident's values hash, gets/adds identifier tp
    the entity's identifiers set

    :param marc_record: MARC record
    :param redis_server: Redis server
    :param field_tag: MARC Field tag, i.e. 020, 028
    :param identifiers_set_key: Key for the entity's identifiers set
    :param subfields_list: List of subfields to check
    :param ident_root: Root of the entity
    """
    all_fields = marc_record.get_fields(field_tag)
    for field in all_fields:
        for subfield in field.get_subfields(subfields_list):
            identifier_key = redis_server.hget("{0}:values".format(ident_root),
                                               subfield)
            if identifier_key is None:
                identifier_key = "{0}:{1}".format(ident_root,
                                                  redis_server.incr("global:{0}".format(ident_root)))
                
                self.redis_server.set(identifier_key,subfield)
                redis_server.hset("{0}:values".format(ident_root),
                                  subfield,
                                  identifier_key)
            redis_server.sadd(identifiers_set_key,identifier_key)

def process_008_date(marc_record,redis_server,date_sort_key):
    """
    Helper function extracts dates from 008 MARC field and
    saves to Redis datastore

    :param marc_record: MARC record
    :param redis_server: Redis datastore instance
    :param date_sort_key: Redis date sort key
    """
    field008 = marc_record['008']
    if field008 is not None:
        field_values = list(field008.value())
        date1 = ''.join(field_values[7:11])
        date2 = ''.join(field_values[11:15])
        if len(date1.strip()) > 0:
            date_search = year_re.search(date1)
            if date_search is not None:
                redis_server.zadd(date_sort_key,
                                  int(date_search.groups()[0]),
                                  date1)
        if len(date2.strip()) > 0:
            date_search = year_re.search(date2)
            if date_search is not None:                
                redis_server.zadd(date_sort_key,
                                  int(date_search.groups()[0]),
                                  date2)
            
def process_tag_list_as_set(marc_record,
                            redis_key,
                            redis_server,
                            tag_list,
                            is_sorted=False):
    """
    Helper function takes a MARC record, a RDA redis key for the set,
    and a listing of MARC Field tags and subfields, and adds each
    TAG-VALUE to the set or sorted set

    :param marc_record: MARC record
    :param redis_key: Redis key for the set or sorted set
    :param redis_server: Redis datastore instance
    :param tag_list: A listing of ('tag','subfield') tuples
    :param is_sorted: Boolean if sorted set, default is False
    """
    for tag in tag_list:
        all_fields = marc_record.get_fields(tag[0])
        for field in all_fields:
            subfields = field.get_subfields(tag[1])
            for subfield in subfields:
                if is_sorted is True:
                    year_search = year_re.search(subfield)
                    if year_search is not None:
                        redis_server.zadd(redis_key,
                                          int(year_search.groups()[0]),
                                          subfield)
                else:
                    redis_server.sadd(redis_key,subfield)
    
            
            
                
                                  
    
    

def ingest_record(marc_record,redis_server):
##    if volatile_redis is None:
##        print("Volatile Redis not available")
##        return None
##    redis_server = volatile_redis
    bib_number = marc_record['907']['a'][1:-1]
    redis_id = redis_server.incr("global:rdaCore")
    redis_key = "rdaCore:%s" % redis_id
    CreateRDACoreWorkFromMARC(record=marc_record,
                              redis_server=redis_server,
                              root_redis_key=redis_key).generate()
    CreateRDACoreExpressionFromMARC(record=marc_record,
                                    redis_server=redis_server,
                                    root_redis_key=redis_key).generate()
    CreateRDACoreManifestationFromMARC(record=marc_record,
                                       redis_server=redis_server,
                                       root_redis_key=redis_key).generate()
    CreateRDACoreItemFromMARC(record=marc_record,
                              redis_server=redis_server,
                              root_redis_key=redis_key).generate()
    
    


def ingest_records(marc_file_location):
    marc_reader = pymarc.MARCReader(open(marc_file_location,"rb"))
    for i,record in enumerate(marc_reader):
        if not i%1000:
            sys.stderr.write(".")
        if not i%10000:
            sys.stderr.write(str(i))
        ingest_record(record)
