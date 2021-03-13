#!/usr/bin/env bash

semver_parse_into () {
  local RE='[^0-9]*\([0-9]*\)[.]\([0-9]*\)[.]\([0-9]*\)\([0-9A-Za-z-]*\)'
  # MAJOR
  eval "${2}"="$(echo "${1}" | sed -e "s#$RE#\1#")"
  # MINOR
  eval "${3}"="$(echo "${1}" | sed -e "s#$RE#\2#")"
  # MINOR
  eval "${4}"="$(echo "${1}" | sed -e "s#$RE#\3#")"
  # SPECIAL
  eval "${5}"="$(echo "${1}" | sed -e "s#$RE#\4#")"
}

MAJOR=0
MINOR=0
PATCH=0
SPECIAL=""

semver_parse_into "${1}" MAJOR MINOR PATCH SPECIAL
printf "%s\n%s\n%s\n%s" "${MAJOR}" "${MINOR}" "${PATCH}" "${SPECIAL}"
