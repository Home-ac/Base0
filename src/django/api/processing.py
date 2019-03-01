import csv
import os
import traceback
import re

import dedupe

from collections import defaultdict
from datetime import datetime
from random import sample

from unidecode import unidecode

from django.conf import settings
from django.contrib.gis.geos import Point

from api.constants import CsvHeaderField, ProcessingAction
from api.models import Facility, FacilityMatch, FacilityList, FacilityListItem
from api.countries import COUNTRY_CODES, COUNTRY_NAMES
from api.geocoding import geocode_address


def parse_csv_line(line):
    return list(csv.reader([line]))[0]


def get_country_code(country):
    # TODO: Handle minor spelling errors in country names
    country = str(country)
    if country.upper() in COUNTRY_NAMES:
        return country.upper()
    elif country.lower() in COUNTRY_CODES:
        return COUNTRY_CODES[country.lower()]
    else:
        raise ValueError(
            'Could not find a country code for "{0}".'.format(country))


def parse_facility_list_item(item):
    started = str(datetime.utcnow())
    if type(item) != FacilityListItem:
        raise ValueError('Argument must be a FacilityListItem')
    if item.status != FacilityListItem.UPLOADED:
        raise ValueError('Items to be parsed must be in the UPLOADED status')
    try:
        fields = [f.lower() for f in parse_csv_line(item.facility_list.header)]
        values = parse_csv_line(item.raw_data)
        if CsvHeaderField.COUNTRY in fields:
            item.country_code = get_country_code(
                values[fields.index(CsvHeaderField.COUNTRY)])
        if CsvHeaderField.NAME in fields:
            item.name = values[fields.index(CsvHeaderField.NAME)]
        if CsvHeaderField.ADDRESS in fields:
            item.address = values[fields.index(CsvHeaderField.ADDRESS)]
        item.status = FacilityListItem.PARSED
        item.processing_results.append({
            'action': ProcessingAction.PARSE,
            'started_at': started,
            'error': False,
            'finished_at': str(datetime.utcnow()),
        })
    except Exception as e:
        item.status = FacilityListItem.ERROR
        item.processing_results.append({
            'action': ProcessingAction.PARSE,
            'started_at': started,
            'error': True,
            'message': str(e),
            'trace': traceback.format_exc(),
            'finished_at': str(datetime.utcnow()),
        })


def geocode_facility_list_item(item):
    started = str(datetime.utcnow())
    if type(item) != FacilityListItem:
        raise ValueError('Argument must be a FacilityListItem')
    if item.status != FacilityListItem.PARSED:
        raise ValueError('Items to be geocoded must be in the PARSED status')
    try:
        data = geocode_address(item.address, item.country_code)
        item.status = FacilityListItem.GEOCODED
        item.geocoded_point = Point(
            data["geocoded_point"]["lng"],
            data["geocoded_point"]["lat"]
        )
        item.geocoded_address = data["geocoded_address"]
        item.processing_results.append({
            'action': ProcessingAction.GEOCODE,
            'started_at': started,
            'error': False,
            'data': data["full_response"],
            'finished_at': str(datetime.utcnow()),
        })
    except Exception as e:
        item.status = FacilityListItem.ERROR
        item.processing_results.append({
            'action': ProcessingAction.GEOCODE,
            'started_at': started,
            'error': True,
            'message': str(e),
            'trace': traceback.format_exc(),
            'finished_at': str(datetime.utcnow()),
        })


def match_facility_list_item(item):
    started = str(datetime.utcnow())
    if type(item) != FacilityListItem:
        raise ValueError('Argument must be a FacilityListItem')
    if item.status != FacilityListItem.GEOCODED:
        raise ValueError('Items to be matched must be in the GEOCODED status')
    try:
        facilities_queryset = Facility.objects.all()

        if facilities_queryset.count() == 0:
            facility = Facility(name=item.name, address=item.geocoded_address,
                                country_code=item.country_code,
                                location=item.geocoded_point,
                                created_from=item)

            facility.save()

            match = FacilityMatch(facility_list_item=item,
                                  facility=facility,
                                  results={
                                     'version': '0', 'match_type': 'exact'
                                  },
                                  confidence=1.0,
                                  status=FacilityMatch.AUTOMATIC)

            match.save()

            item.status = FacilityListItem.MATCHED

            item.processing_results.append({
                'action': ProcessingAction.MATCH,
                'started_at': started,
                'error': False,
                'finished_at': str(datetime.utcnow()),
            })

            return [match]
        elif item.id % 4 == 0:
            facility_ids = [
                f.id
                for f
                in Facility.objects.all()
            ]

            number_of_matches = min(4, len(facility_ids))
            facility_ids_to_match = sample(facility_ids, number_of_matches)

            facilities_queryset = Facility \
                .objects \
                .filter(id__in=facility_ids_to_match)

            matches = []
            for f in facilities_queryset:
                match = FacilityMatch \
                    .objects \
                    .create(facility_list_item=item,
                            facility=f,
                            results={
                                'version': '0',
                                'match_type': 'random',
                            },
                            confidence=0.1,
                            status=FacilityMatch.PENDING)
                match.save()
                matches.append(match)

            item.status = FacilityListItem.POTENTIAL_MATCH
            item.processing_results.append({
                'action': ProcessingAction.MATCH,
                'started_at': started,
                'error': False,
                'finished_at': str(datetime.utcnow()),
            })

            return matches

        matches = Facility.objects.filter(
            country_code=item.country_code, name__iexact=item.name,
            address__iexact=item.geocoded_address)

        if matches.count() == 0:
            facility = Facility(name=item.name,
                                address=item.geocoded_address,
                                country_code=item.country_code,
                                location=item.geocoded_point,
                                created_from=item)
            facility.save()
        else:
            facility = matches[0]

        match = FacilityMatch(facility_list_item=item,
                              facility=facility,
                              results={
                                  'version': '0', 'match_type': 'exact'
                              },
                              confidence=1.0,
                              status=FacilityMatch.AUTOMATIC)

        match.save()

        item.status = FacilityListItem.MATCHED
        item.processing_results.append({
            'action': ProcessingAction.MATCH,
            'started_at': started,
            'error': False,
            'finished_at': str(datetime.utcnow()),
        })

        return [match]
    except Exception as e:
        item.status = FacilityListItem.ERROR
        item.processing_results.append({
            'action': ProcessingAction.MATCH,
            'started_at': started,
            'error': True,
            'message': str(e),
            'trace': traceback.format_exc(),
            'finished_at': str(datetime.utcnow()),
        })
        return None, None


def clean(column):
    """
    Remove punctuation and excess whitespace from a value before using it to
    find matches. This should be the same function used when developing the
    training data read from training.json as part of train_gazetteer.
    """
    column = unidecode(column)
    column = re.sub('\n', ' ', column)
    column = re.sub('-', '', column)
    column = re.sub('/', ' ', column)
    column = re.sub("'", '', column)
    column = re.sub(",", '', column)
    column = re.sub(":", ' ', column)
    column = re.sub(' +', ' ', column)
    column = column.strip().strip('"').strip("'").lower().strip()
    if not column:
        column = None
    return column


def train_gazetteer(messy, canonical):
    """
    Train and return a dedupe.Gazetteer using the specified messy and canonical
    dictionaries. The messy and canonical objects should have the same
    structure:
      - The key is a unique ID
      - The value is another dictionary of field:value pairs. This dictionary
        must contain at least 'country', 'name', and 'address' keys.

    Reads a training.json file containing positive and negative matches.
    """
    fields = [
        {'field': 'country', 'type': 'Exact'},
        {'field': 'name', 'type': 'String'},
        {'field': 'address', 'type': 'String'},
    ]

    gazetteer = dedupe.Gazetteer(fields)
    gazetteer.sample(messy, canonical, 15000)
    training_file = os.path.join(settings.BASE_DIR, 'api', 'data',
                                 'training.json')
    with open(training_file) as tf:
        gazetteer.readTraining(tf)
    gazetteer.train()
    gazetteer.index(canonical)
    gazetteer.cleanupTraining()
    # The gazetteer example in the dedupeio/dedupe-examples repository called
    # index both after training and after calling cleanupTraining.
    gazetteer.index(canonical)
    return gazetteer


def match_facility_list_items(facility_list, automatic_threshold=0.8,
                              recall_weight=1.0):
    """
    Attempt to match all the items in the specified FacilityList that are in
    the GEOCODED status with existing facilities.

    This function reads from but does not update the database.

    Arguments:
    facility_list -- A FacilityList instance
    automatic_threshold -- A number from 0.0 to 1.0. A match with a confidence
                           score greater than this value will be assigned
                           automatically.
    recall_weight -- Sets the tradeoff between precision and recall. A value of
                     1.0 give an equal weight to precision and recall.
                     https://en.wikipedia.org/wiki/Precision_and_recall
                     https://docs.dedupe.io/en/latest/Choosing-a-good-threshold.html

    Returns:
    An dict containing the results of the matching process and contains the
    following keys:

    processed_list_item_ids -- A list of all the FacilityListItem IDs that were
                               considered for matching.
    item_matches -- A dictionary where the keys are FacilityListItem IDs and
                    the values are lists of tuples where the first element is
                    the ID of a Facility that is a potential match and the
                    second element is the confidence score of the match.
    results -- A dictionary containing additional information about the
               matching process that pertains to all the list items and
               contains the following keys:
        gazetteer_threshold -- The threshold computed from the trained model
        automatic_threshold -- The value of the automatic_threshold parameter
                               returned for convenience
        recall_weight -- The value of the recall_weight parameter returned for
                         convenience.
        code_version -- The value of the GIT_COMMIT setting.
    started -- The date and time at which the training and matching was
               started.
    finished -- The date and time at which the training and matching was
                finished.
    """
    started = str(datetime.utcnow())
    if type(facility_list) != FacilityList:
        raise ValueError('Argument must be a FacilityList')

    facility_set = Facility.objects.all().extra(
        select={'country': 'country_code'}).values(
            'id', 'country', 'name', 'address')
    canonical = {str(i['id']): {k: clean(i[k]) for k in i if k != 'id'}
                 for i in facility_set}

    facility_list_item_set = facility_list.facilitylistitem_set.filter(
        status=FacilityListItem.GEOCODED).extra(
            select={'country': 'country_code'}).values(
                'id', 'country', 'name', 'address')
    messy = {str(i['id']): {k: clean(i[k]) for k in i if k != 'id'}
             for i in facility_list_item_set}

    gazetteer = train_gazetteer(messy, canonical)
    threshold = gazetteer.threshold(messy, recall_weight=recall_weight)

    results = gazetteer.match(messy, threshold=threshold, n_matches=None,
                              generator=True)

    finished = str(datetime.utcnow())

    item_matches = defaultdict(list)
    for matches in results:
        for (messy_id, canon_id), score in matches:
            item_matches[messy_id].append((canon_id, score))

    return {
        'processed_list_item_ids': list(messy.keys()),
        'item_matches': item_matches,
        'results': {
            'gazetteer_threshold': threshold.item(),
            'automatic_threshold': automatic_threshold,
            'recall_weight': recall_weight,
            'code_version': settings.GIT_COMMIT
        },
        'started': started,
        'finished': finished
    }


def save_match_details(match_results):
    """
    Save the results of a call to match_facility_list_items by creating
    Facility and FacilityMatch instances and updating the state of the affected
    FacilityListItems.

    Should be called in a transaction to ensure that all the updates are
    applied atomically.

    Arguments:
    match_results -- The dict return value from a call to
                     match_facility_list_items.
    """
    processed_list_item_ids = match_results['processed_list_item_ids']
    item_matches = match_results['item_matches']
    results = match_results['results']
    started = match_results['started']
    finished = match_results['finished']

    automatic_threshold = results['automatic_threshold']

    def make_pending_match(item_id, facility_id, score):
        return FacilityMatch(
            facility_list_item_id=item_id,
            facility_id=facility_id,
            confidence=score,
            status=FacilityMatch.PENDING,
            results=results)

    for item_id, matches in item_matches.items():
        item = FacilityListItem.objects.get(id=item_id)
        item.status = FacilityListItem.POTENTIAL_MATCH
        matches = [make_pending_match(item_id, facility_id, score.item())
                   for facility_id, score in matches]

        if len(matches) == 1:
            if matches[0].confidence >= automatic_threshold:
                matches[0].status = FacilityMatch.AUTOMATIC
                item.status = FacilityListItem.MATCHED
                item.facility = matches[0].facility
        else:
            quality_matches = [m for m in matches
                               if m.confidence > automatic_threshold]
            if len(quality_matches) == 1:
                matches[0].status = FacilityMatch.AUTOMATIC
                item.status = FacilityListItem.MATCHED
                item.facility = matches[0].facility

        item.processing_results.append({
            'action': ProcessingAction.MATCH,
            'started_at': started,
            'error': False,
            'finished_at': finished
        })
        item.save()

        for m in matches:
            m.save()

    unmatched = (FacilityListItem.objects
                 .filter(id__in=processed_list_item_ids)
                 .exclude(id__in=item_matches.keys()))
    for item in unmatched:
        facility = Facility(name=item.name,
                            address=item.address,
                            country_code=item.country_code,
                            location=item.geocoded_point,
                            created_from=item)
        facility.save()

        match = make_pending_match(item.id, facility.id, 1.0)
        match.results['no_gazetteer_match'] = True
        match.status = FacilityMatch.AUTOMATIC
        match.save()

        item.facility = facility
        item.status = FacilityListItem.MATCHED
        item.processing_results.append({
            'action': ProcessingAction.MATCH,
            'started_at': started,
            'error': False,
            'finished_at': finished
        })
        item.save()
