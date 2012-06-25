"""
 :mod:`frbr_redis` Module ingests MARC records into a FRBR-Redis datastore 
 controlled by the MARC Batch app. This Module initially ingests the RDA
 core elements from MARC into datastore.
"""
__author__ = 'Jeremy Nelson'
import pymarc,redis,logging,sys
import re,datetime
from marc_batch.fixures import json_loader


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
        #print("TYPE of {0} {1}".format(type(rule),rule))
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
        redis_incr_value = self.redis_server.incr("global:{0}:{1}".format(self.root_redis_key,
                                                                          entity_name))
        if kwargs.has_key("json_file"):
            self.marc_rules = MARCRules(json_file=kwargs.get('json_file'))
        elif kwargs.has_key("json_rules"):
            self.marc_rules = MARCRules(json_rules=kwargs.get('json_rules'))
        else:
            raise ValueError("CreateRDACoreEntityFromMARC requires json_file or json_rules")
        # Redis Key for this Entity
        self.entity_key = "{0}:{1}:{2}".format(self.root_redis_key,
                                               entity_name,
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
                    self.redis_server.hset(self.entity_key,
                                           element,
                                           values[0])
                else:
                    new_set_key = "{0}:{1}".format(self.entity_key,
                                                   element)
                    for row in values:
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
        else:
            position0,position1 = carrier_value[0],carrier_value[1]
            if carrier_types_dict.has_key(position0):
                    if carrier_types_dict[position0].has_key(position1):
                        self.redis_server.hset(self.entity_key,
                                               'rdaCarrierType',
                                               carrier_types_dict[position0][position1])
    
                    

##    def __identifiers__(self):
##        """
##        Extracts and sets Manifestation's identifiers from MARC record
##        """
##        identifiers_set_key = self.redis_server.hget(self.entity_key,
##                                                     "identifier")
##        if identifiers_set_key is None:
##            identifiers_set_key = "{0}:identifiers".format(self.entity_key)
##        # get/set ISBN
##        process_identifier(self.marc_record,
##                           self.redis_server,
##                           '020',
##                           identifiers_set_key,
##                           ['a','z'],
##                           "isbn")
##        # get/set ISSN
##        process_identifier(self.marc_record,
##                           self.redis_server,
##                           '022',
##                           identifiers_set_key,
##                           ['a','y','z'],
##                           "issn")
##        # get/set ISRC, UPC, ISMN, International Article Number, serial, sources
##        # from 024 field
##        field024s = self.marc_record.get_fields('024')
##        for field in field024s:            
##            # ISRC Identifier
##            if field.indicators[0] == '0':
##                process_identifier(self.marc_record,
##                                   self.redis_server,
##                                   '024',
##                                   identifiers_set_key,
##                                   ['a','d','z'],
##                                   "isrc")
##                    
##            # UPC Identifier
##            elif field.indicators[0] == '1':
##                process_identifier(self.marc_record,
##                                   self.redis_server,
##                                   '024',
##                                   identifiers_set_key,
##                                   ['a','d','z'],
##                                   "upc")
##                    
##            # ISMN Identifier 
##            elif field.indicators[0] == '2':
##                process_identifiers(self.marc_record,
##                                    self.redis_server,
##                                    '024',
##                                    identifiers_set_key,
##                                    ['a','d','z'],
##                                    "ismn")
##                    
##            # International Article Number Identifier
##            elif field.indicators[0] == '3':
##                process_identifiers(self.marc_record,
##                                    self.redis_server,
##                                    '024',
##                                    identifiers_set_key,
##                                    ['a','d','z'],
##                                    "international-article-numbers")                
##                    
##            # Serial Item and Contribution Identiifer
##            elif field.indicators[0] == '4':
##                process_identifiers(self.marc_record,
##                                    self.redis_server,
##                                    '024',
##                                    identifiers_set_key,
##                                    ['a','d','z'],
##                                    "serial-item-contribution-id")                
##            # Source specified in $2
##            elif field.indicators[0] == '7':
##                raw_sources = field.get_subfields('2')
##                for source in raw_sources:
##                    process_identifiers(self.marc_record,
##                                        self.redis_server,
##                                        '024',
##                                        identifiers_set_key,
##                                        ['a','d','z'],
##                                        source)
##            # Unspecified type of standard number or code
##            elif field.indicators[0] == '8':
##                self.redis_server.sadd(identifiers_set_key,
##                                       ''.join(field.get_subfields('a','d','z')))
##        # get/set fingerprint identifier
##        field026s = self.marc_record.get_fields('026')
##        for field in field026s:
##            field_value = ''.join(field.get_subfields('a','b','c','d','e'))
##            fingerprint_schema = field.get_subfields('2')
##            if fingerprint_schema.count('fei') > -1:
##                fingerprint_key = self.redis_server.hget('fei:values',
##                                                         field_values)
##                if fingerprint_key is None:
##                    fingerprint_key = "fei:{0}".format(self.redis_server.incr("global:fei"))
##                    self.redis_server.set(fingerprint_key,field_values)
##                    self.redis_server.hset('fei:values',
##                                           field_values,
##                                           fingerprint_key)
##            elif fingerprint_schema.count('stcnf') > -1:
##                fingerprint_key = self.redis_server.hget('stcnf:values',
##                                                         field_values)
##                if fingerprint_key is None:
##                    fingerprint_key = "stcnf:{0}".format(self.redis_server.incr("global:stcnf"))
##                    self.redis_server.set(fingerprint_key,field_values)
##                    self.redis_server.hset('stcnf:values',
##                                           field_values,
##                                           fingerprint_key)
##            else:
##                fingerprint_key = self.redis_server.hget('fingerprint-other:values',
##                                                         field_values)
##                if fingerprint_key is None:
##                    fingerprint_key = "fingerprint-other:{0}".format(self.redis_server.incr("global:fingerprint-other"))
##                    self.redis_server.set(fingerprint_key,field_values)
##                    self.redis_server.hset('fingerprint-other:values',
##                                           field_values,
##                                           fingerprint_key)
##            self.redis_server.sadd(identifiers_set_key,
##                                   fingerprint_key)
##        # Standard technical report number
##        process_identifier(self.marc_record,
##                           self.redis_server,
##                           '027',
##                           identifiers_set_key,
##                           ['a','z'],
##                           "standard-tech-report")
##        # Videorecording and other publisher number
##        field028s = self.marc_record.get_fields('028')
##        for field in field028s:
##            if field.indicators[0] == '4':
##                process_identifier(self.marc_record,
##                                   self.redis_server,
##                                   '028',
##                                   identifiers_set_key,
##                                   ['a'],
##                                   "videorecording-number")
##            elif field.indicators[0] == '5':
##                process_identifier(self.marc_record,
##                                   self.redis_server,
##                                    '028',
##                                   identifiers_set_key,
##                                   ['a'],
##                                   "other-publisher-number")
##        # CODEN
##        process_identifier(self.marc_record,
##                           self.redis_server,
##                           '030',
##                           identifiers_set_key,
##                           ['a','z'],
##                           "coden")
##        # Stock number
##        process_identifier(self.marc_record,
##                           self.redis_server,
##                           '037',
##                           identifiers_set_key,
##                           ['a'],
##                           "stock-number")
##        # GPO Item number
##        process_identifier(self.marc_record,
##                           self.redis_server,
##                           '074',
##                           identifiers_set_key,
##                           ['a','z'],
##                           "gpo-item")
##        # SUDOC, Gov't of Canada, or other
##        field086s = self.marc_record.get_fields('086')
##        for field in field086s:
##            if field.indicators[0] == '0':
##                process_identifier(self.marc_record,
##                                   self.redis_server,
##                                   '086',
##                                   identifiers_set_key,
##                                   ['a','z'],
##                                   'sudoc')
##            elif field.indicators[0] == '1':
##                process_identifier(self.marc_record,
##                                   self.redis_server,
##                                   '086',
##                                   identifiers_set_key,
##                                   ['a','z'],
##                                   'canada-gov')
##            else:
##                source = field.get_subfields('2')
##                if len(source) > 0:
##                    process_identifier(self.marc_record,
##                                       self.redis_server,
##                                       '086',
##                                       identifiers_set_key,
##                                       ['a','z'],
##                                       source[0])
##        # Report number
##        process_identifier(self.marc_record,
##                           self.redis_server,
##                           '088',
##                           identifiers_set_key,
##                           ["a","z"],
##                           "report-number")
##        # Dissertation identifier
##        process_identifier(self.marc_record,
##                           self.redis_server,
##                           '502',
##                           identifiers_set_key,
##                           ["o"],
##                           "dissertation-idenitifer")
##        
##
##    def __manufacture_statement__(self):
##        manufacture_stmt_key = "{0}:manufactureStatement".format(self.entity_key)
##        place_set_key = self.redis_server.hget(manufacture_stmt_key,
##                                               "placeOfManufacture")
##        if place_set_key is None:
##            place_set_key = "{0}:places".format(manufacture_stmt_key)
##        process_tag_list_as_set(self.marc_record,
##                                place_set_key,
##                                self.redis_server,
##                                [('260','e'),
##                                 ('542','k')])
##        name_set_key = self.redis_server.hget(manufacture_stmt_key,
##                                              "manufactureName")
##        if name_set_key is None:
##            name_set_key = "{0}:names".format(manufacture_stmt_key)
##        process_tag_list_as_set(self.marc_record,
##                                name_set_key,
##                                self.redis_server,
##                                [('260','f'),
##                                 ('542','k')])
##        date_sort_key = self.redis_server.hget(manufacture_stmt_key,
##                                               "dateOfManufacture")
##        if date_sort_key is None:
##            date_sort_key = "{0}:dates".format(manufacture_stmt_key)
##            self.redis_server.hset(manufacture_stmt_key,
##                                   "dateOfManufacture",
##                                   date_sort_key)
##        process_008_date(self.marc_record,
##                         self.redis_server,
##                         date_sort_key)
##        
##
##    def __production_statement__(self):
##        # Production Statement
##        production_stmt_key = "{0}:productionStatement".format(self.entity_key)
##        date_sort_key = self.redis_server.hget(production_stmt_key,
##                                               "dateOfProduction")
##        if date_sort_key is None:
##            date_sort_key = "{0}:dates".format(production_stmt_key)
##            self.redis_server.hset(production_stmt_key,
##                                   "dateOfProduction",
##                                   date_sort_key)
##        process_008_date(self.marc_record,
##                         self.redis_server,
##                         date_sort_key)
##        process_tag_list_as_set(self.marc_record,
##                                date_sort_key,
##                                self.redis_server,
##                                [('260','c'),
##                                 ('542','j')],
##                                is_sorted=True)
##
##    def __publication_statement__(self):
##        # Publication Statement
##        pub_stmt_key = "{0}:publicationStatement".format(self.entity_key)
##        place_set_key = self.redis_server.hget(pub_stmt_key,
##                                               "placeOfPublication")
##        if place_set_key is None:
##            place_set_key = "{0}:places".format(pub_stmt_key)
##            self.redis_server.hset(pub_stmt_key,
##                                   "placeOfPublication",
##                                   place_set_key)
##        process_tag_list_as_set(self.marc_record,
##                                place_set_key,
##                                self.redis_server,
##                                [('260','a'),
##                                 ('542','k'),
##                                 ('542','p')])    
##        pub_name_set_key = self.redis_server.hget(pub_stmt_key,
##                                                  "publisherName")
##        if pub_name_set_key is None:
##            pub_name_set_key = "{0}:publishers".format(pub_stmt_key)
##            self.redis_server.hset(pub_stmt_key,
##                                   "publisherName",
##                                   pub_name_set_key)
##        process_tag_list_as_set(self.marc_record,
##                                pub_name_set_key,
##                                self.redis_server,
##                                [('260','b'),
##                                 ('542','k')])
##        pub_date_key = self.redis_server.hget(pub_stmt_key,
##                                              "dateOfPublication")
##        if pub_date_key is None:
##            pub_date_key = "{0}:dates".format(pub_stmt_key)
##            self.redis_server.hset(pub_stmt_key,
##                                   "dateOfPublication",
##                                   pub_date_key)
##        process_008_date(self.marc_record,
##                         self.redis_server,
##                         pub_date_key)
##        
##
##    def __title_proper__(self):
##        self.redis_server.hset(self.entity_key,
##                               "titleProper",
##                               self.marc_record.title())
##        
##    def __statement_of_responsiblity__(self):
##        # Statement of Responsibility
##        field245s = self.marc_record.get_fields('245')
##        statement_str = ''
##        for field in field245s:
##            subfield_c = field.get_subfields('c')
##            statement_str += "".join(subfield_c)
##            if len(statement_str) > 0:
##                self.redis_server.hset(self.entity_key,
##                                       "statementOfResponsibility",
##                                       statement_str)

class CreateRDACorePersonFromMARC(object):

    def __init__(self,**kwargs):
        if kwargs.has_key('json-rules'):
            self.json_rules = kwargs.get('json-rules')
        else:
            self.json_rules = json_loader['marc-rda-person']

    def load(self):
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
    
            
            
                
                                  
    
    

def ingest_record(marc_record):
    if volatile_redis is None:
        print("Volatile Redis not available")
        return None
    redis_server = volatile_redis
    bib_number = marc_record['907']['a'][1:-1]
    redis_id = redis_server.incr("global:rdaCore")
    redis_key = "rdaCore:%s" % redis_id
    CreateRDACoreWorkFromMARC(record=marc_record,
                              redis_server=redis_server,
                              root_redis_key=redis_key)
    


def ingest_records(marc_file_location):
    if volatile_redis is None:
        return None
    marc_reader = pymarc.MARCReader(open(marc_file_location,"rb"))
    for i,record in enumerate(marc_reader):
        if not i%1000:
            sys.stderr.write(".")
        if not i%10000:
            sys.stderr.write(str(i))
        ingest_record(record)