import csv
import os
import traceback
import re
import sys

import dedupe
import xlrd

from collections import defaultdict
from datetime import datetime
from unidecode import unidecode

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db.models import Q

from api.constants import CsvHeaderField, ProcessingAction
from api.models import Facility, FacilityMatch, FacilityList, FacilityListItem
from api.countries import COUNTRY_CODES, COUNTRY_NAMES
from api.geocoding import geocode_address


def _report_error_to_rollbar(file, request):
    ROLLBAR = getattr(settings, 'ROLLBAR', {})
    if ROLLBAR:
        import rollbar
        rollbar.report_exc_info(
            sys.exc_info(),
            extra_data={
                'user_id': request.user.id,
                'contributor_id': request.user.contributor.id,
                'file_name': file.name})


def get_excel_sheet(file, request):
    import defusedxml
    from defusedxml.common import EntitiesForbidden

    defusedxml.defuse_stdlib()

    try:
        return xlrd.open_workbook(file_contents=file.read(),
                                  on_demand=True).sheet_by_index(0)
    except EntitiesForbidden:
        _report_error_to_rollbar(file, request)
        raise ValidationError('This file may be damaged and '
                              'cannot be processed safely')


def parse_excel(file, request):
    try:
        sheet = get_excel_sheet(file, request)

        header = ','.join(sheet.row_values(0))
        rows = ['"{}"'.format('","'.join(sheet.row_values(idx)))
                for idx in range(1, sheet.nrows)]

        return header, rows
    except Exception:
        _report_error_to_rollbar(file, request)
        raise ValidationError('Error parsing Excel file')


def parse_csv(file, request):
    rows = []

    try:
        header = file.readline().decode(encoding='utf-8-sig').rstrip()
    except UnicodeDecodeError:
        _report_error_to_rollbar(file, request)
        raise ValidationError('Unsupported file encoding. Please '
                              'submit a UTF-8 CSV.')

    for idx, line in enumerate(file):
        if idx > 0:
            try:
                rows.append(line.decode(encoding='utf-8-sig').rstrip())
            except UnicodeDecodeError:
                _report_error_to_rollbar(file, request)
                raise ValidationError('Unsupported file encoding. Please '
                                      'submit a UTF-8 CSV.')

    return header, rows


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
        is_geocoded = False
        fields = [f.lower() for f in parse_csv_line(item.facility_list.header)]
        values = parse_csv_line(item.raw_data)
        if CsvHeaderField.COUNTRY in fields:
            item.country_code = get_country_code(
                values[fields.index(CsvHeaderField.COUNTRY)])
        if CsvHeaderField.NAME in fields:
            item.name = values[fields.index(CsvHeaderField.NAME)]
        if CsvHeaderField.ADDRESS in fields:
            item.address = values[fields.index(CsvHeaderField.ADDRESS)]
        if CsvHeaderField.LAT in fields and CsvHeaderField.LNG in fields:
            lat = float(values[fields.index(CsvHeaderField.LAT)])
            lng = float(values[fields.index(CsvHeaderField.LNG)])
            item.geocoded_point = Point(lng, lat)
            is_geocoded = True
        try:
            item.full_clean(exclude=('processing_started_at',
                                     'processing_completed_at',
                                     'processing_results', 'geocoded_point',
                                     'facility'))
            item.status = FacilityListItem.PARSED
            item.processing_results.append({
                'action': ProcessingAction.PARSE,
                'started_at': started,
                'error': False,
                'finished_at': str(datetime.utcnow()),
                'is_geocoded': is_geocoded,
            })
        except ValidationError as ve:
            messages = []
            for name, errors in ve.error_dict.items():
                # We need to clear the invalid value so we can save the row
                setattr(item, name, '')
                error_str = ''.join(''.join(e.messages) for e in errors)
                messages.append(
                    'There is a problem with the {0}: {1}'.format(name,
                                                                  error_str)
                )
            item.status = FacilityListItem.ERROR_PARSING
            item.processing_results.append({
                'action': ProcessingAction.PARSE,
                'started_at': started,
                'error': True,
                'message': '\n'.join(messages),
                'trace': traceback.format_exc(),
                'finished_at': str(datetime.utcnow()),
            })
    except Exception as e:
        item.status = FacilityListItem.ERROR_PARSING
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
        if item.geocoded_point is None:
            data = geocode_address(item.address, item.country_code)
            if data['result_count'] > 0:
                item.status = FacilityListItem.GEOCODED
                item.geocoded_point = Point(
                    data["geocoded_point"]["lng"],
                    data["geocoded_point"]["lat"]
                )
                item.geocoded_address = data["geocoded_address"]
            else:
                item.status = FacilityListItem.GEOCODED_NO_RESULTS
            item.processing_results.append({
                'action': ProcessingAction.GEOCODE,
                'started_at': started,
                'error': False,
                'skipped_geocoder': False,
                'data': data['full_response'],
                'finished_at': str(datetime.utcnow()),
               })
        else:
            item.status = FacilityListItem.GEOCODED
            item.geocoded_address = item.address
            item.processing_results.append({
                'action': ProcessingAction.GEOCODE,
                'started_at': started,
                'error': False,
                'skipped_geocoder': True,
                'finished_at': str(datetime.utcnow()),
            })

    except Exception as e:
        item.status = FacilityListItem.ERROR_GEOCODING
        item.processing_results.append({
            'action': ProcessingAction.GEOCODE,
            'started_at': started,
            'error': True,
            'message': str(e),
            'trace': traceback.format_exc(),
            'finished_at': str(datetime.utcnow()),
        })


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
    settings_file = os.path.join(settings.BASE_DIR, 'api', 'data',
                                 'gazetteer_model_settings')
    if os.path.exists(settings_file):
        with open(settings_file, 'rb') as sf:
            gazetteer = dedupe.StaticGazetteer(sf)
    else:
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
        training_start = datetime.now()
        gazetteer.train()
        training_duration = datetime.now() - training_start
        print('training_duration ', training_duration)

        with open(settings_file, 'wb') as sf:
            gazetteer.writeSettings(sf)

    index_start = datetime.now()
    gazetteer.index(canonical)
    index_duration = datetime.now() - index_start
    print('index_duration ', index_duration)

    if isinstance(gazetteer, dedupe.Gazetteer):
        gazetteer.cleanupTraining()
        # The gazetteer example in the dedupeio/dedupe-examples repository
        # called index both after training and after calling cleanupTraining.
        gazetteer.index(canonical)

    return gazetteer


class MatchDefaults:
    AUTOMATIC_THRESHOLD = 0.8
    GAZETTEER_THRESHOLD = 0.5
    RECALL_WEIGHT = 1.0


def match_items(messy,
                canonical,
                automatic_threshold=MatchDefaults.AUTOMATIC_THRESHOLD,
                gazetteer_threshold=MatchDefaults.GAZETTEER_THRESHOLD,
                recall_weight=MatchDefaults.RECALL_WEIGHT):
    """
    Attempt to match each of the "messy" items specified with a "canonical"
    item.

    This function reads from but does not update the database.

    When an argument description mentions a "clean" value it is referring to a
    value that has been passed through the `clean` function.

    Arguments:
    messy -- A dictionary. The key is the unique identifier of each item to be
             matched. The value is a dictionary of clean field values keyed by
             field name (country, name, address).
    canonical -- A dictionary. The key is the unique identifier of each
                 potential match target. The value is a dictionary of clean
                 field values keyed by field name (country, name, address).
    automatic_threshold -- A number from 0.0 to 1.0. A match with a confidence
                           score greater than this value will be assigned
                           automatically.
    gazetteer_threshold -- A number from 0.0 to 1.0. A match with a confidence
                           score between this value and the
                           `automatic_threshold` will be considers a match that
                           requires confirmation.
    recall_weight -- Sets the tradeoff between precision and recall. A value of
                     1.0 give an equal weight to precision and recall.
                     https://en.wikipedia.org/wiki/Precision_and_recall
                     https://docs.dedupe.io/en/latest/Choosing-a-good-threshold.html

    Returns:
    An dict containing the results of the matching process and contains the
    following keys:

    processed_list_item_ids -- A list of all the keys in `messy` that were
                               considered for matching.
    item_matches -- A dictionary where the keys are `messy` keys and the values
                    are lists of tuples where the first element is a key from
                    `canonical` representing an item that is a potential match
                    and the second element is the confidence score of the
                    match.
    results -- A dictionary containing additional information about the
               matching process that pertains to all the `messy` items and
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
    if len(canonical.keys()) > 0 and len(messy.keys()) > 0:
        no_geocoded_items = False
        gazetteer = train_gazetteer(messy, canonical)
        try:
            gazetteer.threshold(messy, recall_weight=recall_weight)
            results = gazetteer.match(messy, threshold=gazetteer_threshold,
                                      n_matches=None, generator=True)
            no_gazetteer_matches = False
        except dedupe.core.BlockingError:
            results = []
            no_gazetteer_matches = True
    else:
        results = []
        no_gazetteer_matches = len(canonical.keys()) == 0
        no_geocoded_items = len(messy.keys()) == 0

    finished = str(datetime.utcnow())

    item_matches = defaultdict(list)
    for matches in results:
        for (messy_id, canon_id), score in matches:
            item_matches[messy_id].append((canon_id, score))

    return {
        'processed_list_item_ids': list(messy.keys()),
        'item_matches': item_matches,
        'results': {
            'no_gazetteer_matches': no_gazetteer_matches,
            'no_geocoded_items': no_geocoded_items,
            'gazetteer_threshold': gazetteer_threshold,
            'automatic_threshold': automatic_threshold,
            'recall_weight': recall_weight,
            'code_version': settings.GIT_COMMIT
        },
        'started': started,
        'finished': finished
    }


def get_canonical_items():
    """
    Fetch all `Facility` items and create a dictionary suitable for use by a
    Dedupe model.

    Returns:
    A dictionary. The key is the `Facility` OAR ID. The value is a dictionary
    of clean field values keyed by field name (country, name, address). A
    "clean" value is one which has been passed through the `clean` function.
    """
    facility_set = Facility.objects.all().extra(
        select={'country': 'country_code'}).values(
            'id', 'country', 'name', 'address')
    return {str(i['id']): {k: clean(i[k]) for k in i if k != 'id'}
            for i in facility_set}


def get_messy_items_from_facility_list(facility_list):
    """
    Fetch all `FacilityListItem` objects that belong to the specified
    `FacilityList` and create a dictionary suitable for use by a Dedupe model.

    Arguments:
    facility_list -- A `FacilityList`.

    Returns:
    A dictionary. The key is the `FacilityListItem` ID. The value is a
    dictionary of clean field values keyed by field name (country, name,
    address). A "clean" value is one which has been passed through the `clean`
    function.
    """
    facility_list_item_set = facility_list.facilitylistitem_set.filter(
        Q(status=FacilityListItem.GEOCODED)
        | Q(status=FacilityListItem.GEOCODED_NO_RESULTS)).extra(
            select={'country': 'country_code'}).values(
                'id', 'country', 'name', 'address')
    return {str(i['id']): {k: clean(i[k]) for k in i if k != 'id'}
            for i in facility_list_item_set}


def match_facility_list_items(
        facility_list,
        automatic_threshold=MatchDefaults.AUTOMATIC_THRESHOLD,
        gazetteer_threshold=MatchDefaults.GAZETTEER_THRESHOLD,
        recall_weight=MatchDefaults.RECALL_WEIGHT):

    if type(facility_list) != FacilityList:
        raise ValueError('Argument must be a FacilityList')

    return match_items(get_messy_items_from_facility_list(facility_list),
                       get_canonical_items(),
                       automatic_threshold,
                       gazetteer_threshold,
                       recall_weight)


def match_item(country,
               name,
               address,
               id='id',
               automatic_threshold=MatchDefaults.AUTOMATIC_THRESHOLD,
               gazetteer_threshold=MatchDefaults.GAZETTEER_THRESHOLD,
               recall_weight=MatchDefaults.RECALL_WEIGHT):
    return match_items(
        {
            str(id): {
                "country": clean(country),
                "name": clean(name),
                "address": clean(address)
            }
        },
        get_canonical_items(),
        automatic_threshold,
        gazetteer_threshold,
        recall_weight)


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
                matches[0].results['match_type'] = 'single_gazetteer_match'
                item.status = FacilityListItem.MATCHED
                item.facility = matches[0].facility
        else:
            quality_matches = [m for m in matches
                               if m.confidence > automatic_threshold]
            if len(quality_matches) == 1:
                matches[0].status = FacilityMatch.AUTOMATIC
                matches[0].results['match_type'] = \
                    'one_gazetteer_match_greater_than_threshold'
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
        if item.status == FacilityListItem.GEOCODED_NO_RESULTS:
            item.status = FacilityListItem.ERROR_MATCHING
            item.processing_results.append({
                'action': ProcessingAction.MATCH,
                'started_at': started,
                'error': True,
                'message': ('No match to an existing facility and cannot '
                            'create a new facility without a geocode result'),
                'finished_at': finished
            })
        else:
            facility = Facility(name=item.name,
                                address=item.address,
                                country_code=item.country_code,
                                location=item.geocoded_point,
                                created_from=item)
            facility.save()

            match = make_pending_match(item.id, facility.id, 1.0)
            match.results['match_type'] = 'no_gazetteer_match'
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
