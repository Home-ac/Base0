import { createAction } from 'redux-act';
import mapValues from 'lodash/mapValues';
import isNull from 'lodash/isNull';

import csrfRequest from '../util/csrfRequest';

import {
    logErrorAndDispatchFailure,
    makeGetOrUpdateApprovedFacilityClaimURL,
} from '../util/util';

export const startFetchClaimedFacilityDetails =
    createAction('START_FETCH_CLAIMED_FACILITY_DETAILS');
export const failFetchClaimedFacilityDetails =
    createAction('FAIL_FETCH_CLAIMED_FACILITY_DETAILS');
export const completeFetchClaimedFacilityDetails =
    createAction('COMPLETE_FETCH_CLAIMED_FACILITY_DETAILS');
export const clearClaimedFacilityDetails =
    createAction('CLEAR_CLAIMED_FACILITY_DETAILS');

export function fetchClaimedFacilityDetails(claimID) {
    return (dispatch) => {
        if (!claimID) {
            return null;
        }

        dispatch(startFetchClaimedFacilityDetails());

        return csrfRequest
            .get(makeGetOrUpdateApprovedFacilityClaimURL(claimID))
            .then(({ data }) => mapValues(data, (v) => {
                if (isNull(v)) {
                    return '';
                }

                return v;
            }))
            .then(data => dispatch(completeFetchClaimedFacilityDetails(data)))
            .catch(err => dispatch(logErrorAndDispatchFailure(
                err,
                'An error prevented fetching details about that claimed facility',
                failFetchClaimedFacilityDetails,
            )));
    };
}

export const startUpdateClaimedFacilityDetails =
    createAction('START_UPDATE_CLAIMED_FACILITY_DETAILS');
export const failUpdateClaimedFacilityDetails =
    createAction('FAIL_UPDATE_CLAIMED_FACILITY_DETAILS');
export const completeUpdateClaimedFacilityDetails =
    createAction('COMPLETE_UPDATE_CLAIMED_FACILITY_DETAILS');

export function submitClaimedFacilityDetailsUpdate(claimID) {
    return (dispatch, getState) => {
        const {
            claimedFacilityDetails: {
                data,
            },
        } = getState();

        if (!data || !claimID) {
            return null;
        }

        dispatch(startUpdateClaimedFacilityDetails());

        return csrfRequest
            .put(makeGetOrUpdateApprovedFacilityClaimURL(claimID), data)
            .then(({ data: responseData }) => mapValues(responseData, (v) => {
                if (isNull(v)) {
                    return '';
                }

                return v;
            }))
            .then(responseData => dispatch(completeUpdateClaimedFacilityDetails(responseData)))
            .catch(err => dispatch(logErrorAndDispatchFailure(
                err,
                'An error prevented updating that facility claim\'s details',
                failUpdateClaimedFacilityDetails,
            )));
    };
}

export const updateClaimedFacilityName =
    createAction('UPDATE_CLAIMED_FACILITY_NAME');
export const updateClaimedFacilityAddress =
    createAction('UPDATE_CLAIMED_FACILITY_ADDRESS');
export const updateClaimedFacilityPhone =
    createAction('UPDATE_CLAIMED_FACILITY_PHONE');
export const updateClaimedFacilityPhoneVisibility =
    createAction('UPDATE_CLAIMED_FACILITY_PHONE_VISIBILITY');
export const updateClaimedFacilityWebsite =
    createAction('UPDATE_CLAIMED_FACILITY_WEBSITE');
export const updateClaimedFacilityDescription =
    createAction('UPDATE_CLAIMED_FACILITY_DESCRIPTION');
export const updateClaimedFacilityMinimumOrder =
    createAction('UPDATE_CLAIMED_FACILITY_MINIMUM_ORDER');
export const updateClaimedFacilityAverageLeadTime =
    createAction('UPDATE_CLAIMED_FACILITY_AVERAGE_LEAD_TIME');
export const updateClaimedFacilityPointOfContactVisibility =
    createAction('UPDATE_CLAIMED_FACILITY_POINT_OF_CONTACT_VISIBILITY');
export const updateClaimedFacilityContactPersonName =
    createAction('UPDATE_CLAIMED_FACILITY_CONTACT_PERSON_NAME');
export const updateClaimedFacilityContactEmail =
    createAction('UPDATE_CLAIMED_FACILITY_CONTACT_EMAIL');
export const updateClaimedFacilityOfficeVisibility =
    createAction('UPDATE_CLAIMED_FACILITY_OFFICE_VISIBILITY');
export const updateClaimedFacilityOfficeName =
    createAction('UPDATE_CLAIMED_FACILITY_OFFICE_NAME');
export const updateClaimedFacilityOfficeAddress =
    createAction('UPDATE_CLAIMED_FACILITY_OFFICE_ADDRESS');
export const updateClaimedFacilityOfficeCountry =
    createAction('UPDATE_CLAIMED_FACILITY_OFFICE_COUNTRY');
export const updateClaimedFacilityOfficePhone =
    createAction('UPDATE_CLAIMED_FACILITY_OFFICE_PHONE');
