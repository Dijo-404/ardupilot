#!/usr/bin/env python3

'''
Validate onboard logging documentation.

This script combines parse.py generation with validation logic to ensure
all log messages have proper documentation.

AP_FLAKE8_CLEAN
'''

import argparse
import os
import re
import sys
import tempfile

import parse

topdir = os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../../')
topdir = os.path.realpath(topdir)


def find_LogStructureFiles(rootdir):
    '''return list of files named LogStructure.h'''
    ret = []
    for root, _, files in os.walk(rootdir):
        for f in files:
            if f == 'LogStructure.h':
                ret.append(os.path.join(root, f))
            if f == 'LogStructure_SBP.h':
                ret.append(os.path.join(root, f))
    return ret


def find_format_defines(lines):
    '''find format/label/unit/mult defines in source files'''
    ret = {}
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        m = re.match(r'#define (\w+_(?:LABELS|FMT|UNITS|MULTS))\s+(".*")', line)
        if m is None:
            continue
        (a, b) = (m.group(1), m.group(2))
        if a in ret:
            raise ValueError("Duplicate define for (%s)" % a)
        ret[a] = b
    return ret


def vehicle_code_dirpath(vehicle, rootdir):
    '''returns path to vehicle-specific code directory'''
    vehicle_map = {
        "Copter": "ArduCopter",
        "Plane": "ArduPlane",
        "Rover": "Rover",
        "Sub": "ArduSub",
        "Tracker": "AntennaTracker",
        "Blimp": "Blimp",
    }
    dirname = vehicle_map.get(vehicle, vehicle)
    return os.path.join(rootdir, dirname)


# State machine constants
STATE_OUTSIDE = 0
STATE_INSIDE = 1
LINESTATE_NONE = 0
LINESTATE_WITHIN = 1


def all_log_format_ids(vehicle, rootdir):
    '''Parse C++ code to extract definitions of log messages.'''
    structure_files = find_LogStructureFiles(rootdir)
    structure_lines = []
    for f in structure_files:
        with open(f) as fd:
            structure_lines.extend(fd.readlines())

    defines = find_format_defines(structure_lines)

    ids = {}
    message_infos = []

    for f in structure_files:
        print("Parsing structure file: %s" % f)
        state = STATE_OUTSIDE
        linestate = LINESTATE_NONE

        with open(f) as fd:
            for line in fd.readlines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                line = re.sub("//.*", "", line)
                if re.match(r"\s*$", line):
                    continue

                if state == STATE_OUTSIDE:
                    if ("#define LOG_COMMON_STRUCTURES" in line or
                            re.match("#define LOG_STRUCTURE_FROM_.*", line) or
                            re.match("#define LOG_RTC_MESSAGE.*", line)):
                        state = STATE_INSIDE
                    continue

                if state == STATE_INSIDE:
                    if linestate == LINESTATE_NONE:
                        allowed_list = [
                            'LOG_STRUCTURE_FROM_',
                            'LOG_RTC_MESSAGE',
                        ]
                        allowed = False
                        for a in allowed_list:
                            if a in line:
                                allowed = True
                        if allowed:
                            continue

                        m = re.match(r"\s*{(.*)},\s*", line)
                        if m is not None:
                            message_infos.append(m.group(1))
                            continue

                        m = re.match(r"\s*{(.*)\\\s*$", line)
                        if m is None:
                            state = STATE_OUTSIDE
                            continue

                        partial_line = m.group(1)
                        linestate = LINESTATE_WITHIN
                        continue

                    if linestate == LINESTATE_WITHIN:
                        # Match closing brace, optional comma, optional backslash continuation
                        m = re.match(r"(.*)}[,\s\\]*$", line)
                        if m is None:
                            line = line.rstrip()
                            # Remove trailing backslash if present
                            newline = re.sub(r"\\$", "", line)
                            if newline == line:
                                raise ValueError("Expected backslash at end of line")
                            line = newline.rstrip()
                            # cpp-style string concatenation
                            line = re.sub(r'"\s*"', '', line)
                            partial_line += line
                            continue

                        message_infos.append(partial_line + m.group(1))
                        linestate = LINESTATE_NONE
                        continue

        if linestate != LINESTATE_NONE:
            raise ValueError("Must be linestate-none at end of file")

    filepath = os.path.join(vehicle_code_dirpath(vehicle, rootdir), "Log.cpp")
    state = STATE_OUTSIDE
    linestate = LINESTATE_NONE

    with open(filepath, 'rb') as fd:
        for line in fd.readlines():
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            line = re.sub("//.*", "", line)
            if re.match(r"\s*$", line):
                continue

            if state == STATE_OUTSIDE:
                if ("const LogStructure" in line or
                        "const struct LogStructure" in line):
                    state = STATE_INSIDE
                continue

            if state == STATE_INSIDE:
                if re.match("};", line):
                    state = STATE_OUTSIDE
                    break

                if linestate == LINESTATE_NONE:
                    if "#if HAL_QUADPLANE_ENABLED" in line:
                        continue
                    if "#if FRAME_CONFIG == HELI_FRAME" in line:
                        continue
                    if "#if AC_PRECLAND_ENABLED" in line:
                        continue
                    if "#if AP_PLANE_OFFBOARD_GUIDED_SLEW_ENABLED" in line:
                        continue
                    if "#end" in line:
                        continue
                    if "LOG_COMMON_STRUCTURES" in line:
                        continue

                    m = re.match(r"\s*{(.*)},\s*", line)
                    if m is not None:
                        message_infos.append(m.group(1))
                        continue

                    m = re.match(r"\s*{(.*)", line)
                    if m is None:
                        raise ValueError("Bad line %s" % line)
                    partial_line = m.group(1)
                    linestate = LINESTATE_WITHIN
                    continue

                if linestate == LINESTATE_WITHIN:
                    m = re.match(r"(.*)},?\s*$", line)
                    if m is None:
                        line = line.rstrip()
                        # Remove trailing backslash if present
                        line = re.sub(r"\\$", "", line)
                        line = line.rstrip()
                        # cpp-style string concatenation
                        line = re.sub(r'"\s*"', '', line)
                        partial_line += line
                        continue

                    message_infos.append(partial_line + m.group(1))
                    linestate = LINESTATE_NONE
                    continue

    if state == STATE_INSIDE:
        raise ValueError("Should not be in state_inside at end")

    for message_info in message_infos:
        for define in defines:
            message_info = re.sub(define, defines[define], message_info)
        m = re.match(r'\s*LOG_\w+\s*,\s*(?:sizeof|RLOG_SIZE)\([^)]+\)\s*,\s*"(\w+)"\s*,\s*"(\w+)"\s*,\s*"([\w,]+)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*(,\s*(true|false))?\s*$', message_info)  # noqa
        if m is None:
            continue
        (name, fmt, labels, units, multipliers) = (m.group(1), m.group(2), m.group(3), m.group(4), m.group(5))
        if name in ids:
            raise ValueError("Already seen a (%s) message" % name)
        ids[name] = {
            "name": name,
            "format": fmt,
            "labels": labels,
            "units": units,
            "multipliers": multipliers,
        }

    base_directories = [
        os.path.join(rootdir, 'libraries'),
        vehicle_code_dirpath(vehicle, rootdir),
    ]
    log_write_statements = []

    for base_directory in base_directories:
        for root, dirs, files in os.walk(base_directory):
            state = STATE_OUTSIDE

            for f in files:
                if not re.search("[.]cpp$", f):
                    continue
                filepath = os.path.join(root, f)
                if "AP_Logger/examples" in filepath:
                    continue

                with open(filepath, 'rb') as fd:
                    log_write_statement = ""
                    for line in fd.readlines():
                        if isinstance(line, bytes):
                            line = line.decode("utf-8")

                        if state == STATE_OUTSIDE:
                            if (re.match(r"\s*AP::logger\(\)[.]Write(?:Streaming)?\(", line) or
                                    re.match(r"\s*logger[.]Write(?:Streaming)?\(", line)):
                                state = STATE_INSIDE
                                line = re.sub("//.*", "", line)
                                log_write_statement = line
                            continue

                        if state == STATE_INSIDE:
                            line = re.sub("//.*", "", line)
                            line = re.sub(r'"\s*"', '', line)
                            log_write_statement += line
                            if re.match(r".*\);", line):
                                log_write_statements.append(log_write_statement)
                                state = STATE_OUTSIDE

                    if state != STATE_OUTSIDE:
                        raise ValueError("Expected to be outside at end of file")

    log_write_statements = [re.sub(r"\s+", " ", x) for x in log_write_statements]
    results = []

    for log_write_statement in log_write_statements:
        for define in defines:
            log_write_statement = re.sub(define, defines[define], log_write_statement)

        my_re = r' logger[.]Write(?:Streaming)?\(\s*"(\w+)"\s*,\s*"([\w,]+)".*\);'
        m = re.match(my_re, log_write_statement)
        if m is None:
            my_re = r' AP::logger\(\)[.]Write(?:Streaming)?\(\s*"(\w+)"\s*,\s*"([\w,]+)".*\);'
            m = re.match(my_re, log_write_statement)
        if m is None:
            raise ValueError("Did not match (%s) with (%s)" % (log_write_statement, str(my_re)))
        else:
            results.append((m.group(1), m.group(2)))

    for result in results:
        (name, labels) = result
        if name in ids:
            raise Exception("Already have id for (%s)" % name)
        ids[name] = {
            "name": name,
            "labels": labels,
        }

    if len(ids) == 0:
        raise ValueError("Did not get any ids")

    return ids


def get_whitelist(vehicle):
    '''Return a set of messages which we do not want to see documentation for.'''
    ret = set()

    # Get the directory name for consistency with vehicle_code_dirpath
    vinfo_key = os.path.basename(vehicle_code_dirpath(vehicle, topdir))

    if vinfo_key != 'ArduPlane' and vinfo_key != 'ArduCopter' and vinfo_key != 'Helicopter':
        ret.update([
            "ATUN",
        ])
    if vinfo_key != 'ArduPlane':
        ret.update([
            "TECS",
            "TEC2",
            "TEC3",
            "TEC4",
            "SOAR",
            "SORC",
            "QBRK",
            "FWDT",
            "VAR",
        ])
    if vinfo_key != 'ArduCopter' and vinfo_key != 'Helicopter':
        ret.update([
            "ARHS",
            "AROT",
            "ARSC",
            "ATDH",
            "ATNH",
            "ATSH",
            "GMB1",
            "GMB2",
            "SURF",
        ])

    return ret


def validate_logger_documentation(vehicle, output_dir):
    '''
    Validate logger documentation for a vehicle.

    Generates XML documentation using parse.py, then compares documented
    messages against those found in C++ source code.

    Returns True if validation passes, False otherwise.
    '''
    xml_filepath = os.path.join(output_dir, "LogMessages.xml")

    try:
        os.unlink(xml_filepath)
    except OSError:
        # It's acceptable if the XML file does not exist yet or cannot be removed.
        pass

    print(f"Generating documentation for {vehicle}...")

    old_cwd = os.getcwd()
    try:
        os.chdir(output_dir)

        docgen = parse.LoggerDocco(vehicle)

        if vehicle not in docgen.vehicle_map:
            print(f"Invalid vehicle: {vehicle}")
            print(f"Valid vehicles: {list(docgen.vehicle_map.keys())}")
            return False

        docgen.run()
    finally:
        os.chdir(old_cwd)

    if not os.path.exists(xml_filepath):
        print(f"ERROR: Failed to generate {xml_filepath}")
        return False

    length = os.path.getsize(xml_filepath)
    min_length = 1024
    if length < min_length:
        print(f"ERROR: Generated XML file is too short ({length} < {min_length} bytes)")
        return False

    print(f"Generated XML file: {length} bytes")

    from lxml import objectify
    with open(xml_filepath, 'rb') as f:
        xml = f.read()
    objectify.enable_recursive_str()
    tree = objectify.fromstring(xml)

    whitelist = get_whitelist(vehicle)

    docco_ids = {}
    for thing in tree.logformat:
        name = str(thing.get("name"))
        docco_ids[name] = {
            "name": name,
            "labels": [],
        }
        if getattr(thing.fields, 'field', None) is None:
            if name in whitelist:
                continue
            print(f"ERROR: No doc fields for {name}")
            return False

        for field in thing.fields.field:
            fieldname = field.get("name")
            docco_ids[name]["labels"].append(fieldname)

    print("Parsing C++ code to extract log message definitions...")
    code_ids = all_log_format_ids(vehicle, topdir)

    undocumented = set()
    overdocumented = set()

    for name in sorted(code_ids.keys()):
        if name not in docco_ids:
            if name not in whitelist:
                undocumented.add(name)
            continue

        if name in whitelist:
            overdocumented.add(name)

        seen_labels = {}
        for label in code_ids[name]["labels"].split(","):
            if label in seen_labels:
                print(f"ERROR: {name}.{label} is duplicate label")
                return False
            seen_labels[label] = True

            if label not in docco_ids[name]["labels"]:
                msg = f"{name}.{label} not in documented fields (have ({','.join(docco_ids[name]['labels'])}))"
                if name in whitelist:
                    print(f"WARNING: {msg}")
                    overdocumented.discard(name)
                    continue
                print(f"ERROR: {msg}")
                return False

    if len(undocumented):
        print("ERROR: Undocumented messages found:")
        for name in sorted(undocumented):
            print(f"  - {name}")
        return False

    if len(overdocumented):
        print("ERROR: Messages documented when they shouldn't be:")
        for name in sorted(overdocumented):
            print(f"  - {name}")
        return False

    missing = []
    for name in sorted(docco_ids):
        if name not in code_ids and name not in whitelist:
            missing.append(name)
            continue
        if name not in code_ids:
            # name is in whitelist but not in code, skip label validation
            continue

        for label in docco_ids[name]["labels"]:
            if label not in code_ids[name]["labels"].split(","):
                print(f"ERROR: Documented field {name}.{label} not found in code")
                return False

    if len(missing) > 0:
        print(f"ERROR: Documented messages not in code: {missing}")
        return False

    print(f"SUCCESS: Logger documentation validation passed for {vehicle}")
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Validate onboard logging documentation"
    )
    parser.add_argument(
        "--vehicle",
        required=True,
        help="Vehicle type (Copter, Plane, Rover, Sub, Tracker, Blimp)"
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for generated files (default: temp directory)"
    )

    args = parser.parse_args()

    cleanup_temp_dir = False
    if args.output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="logger_validation_")
        cleanup_temp_dir = True
    else:
        output_dir = args.output_dir
        os.makedirs(output_dir, exist_ok=True)

    try:
        success = validate_logger_documentation(args.vehicle, output_dir)
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if cleanup_temp_dir:
            import shutil
            shutil.rmtree(output_dir, ignore_errors=True)
