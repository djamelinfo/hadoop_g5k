import os
import re
import shutil
import tempfile

import xml.etree.ElementTree as ET
from execo import SshProcess

from execo.action import Remote, TaktukRemote
from execo.host import Host
from execo.log import style
from execo_engine import logger
from execo_g5k import get_oar_job_nodes, get_oargrid_job_nodes


# Imports #####################################################################

def import_class(name):
    """Dynamically load a class and return a reference to it.

    Args:
      name (str): the class name, including its package hierarchy.

    Returns:
      A reference to the class.
    """

    last_dot = name.rfind(".")
    package_name = name[:last_dot]
    class_name = name[last_dot + 1:]

    mod = __import__(package_name, fromlist=[class_name])
    return getattr(mod, class_name)


def import_function(name):
    """Dynamically load a function and return a reference to it.

    Args:
      name (str): the function name, including its package hierarchy.

    Returns:
      A reference to the function.
    """

    last_dot = name.rfind(".")
    package_name = name[:last_dot]
    function_name = name[last_dot + 1:]

    mod = __import__(package_name, fromlist=[function_name])
    return getattr(mod, function_name)


# Requirements ################################################################

def check_java_version(java_major_version, hosts):

    tr = TaktukRemote("java -version 2>&1 | grep version", hosts)
    tr.run()

    for p in tr.processes:
        match = re.match('.*[^.0-9]1\.([0-9]+).[0-9].*', p.stdout)
        version = int(match.group(1))
        if java_major_version > version:
            msg = "Java 1.%d+ required" % java_major_version
            return False

    return True


def get_java_home(host):
    proc = SshProcess('echo $(readlink -f /usr/bin/javac | '
                               'sed "s:/bin/javac::")', host)
    proc.run()
    return proc.stdout.strip()


def check_packages(packages, hosts):
    tr = TaktukRemote("dpkg -s " + packages, hosts)
    for p in tr.processes:
        p.nolog_exit_code = p.nolog_error = True
    tr.run()
    return tr.ok


# Compression #################################################################

def uncompress(file_name, host):
    if file_name.endswith("tar.gz"):
        decompression = Remote("tar xf " + file_name, [host])
        decompression.run()

        base_name = os.path.basename(file_name[:-7])
        dir_name = os.path.dirname(file_name[:-7])
        new_name = dir_name + "/data-" + base_name

        action = Remote("mv " + file_name[:-7] + " " + new_name, [host])
        action.run()
    elif file_name.endswith("gz"):
        decompression = Remote("gzip -d " + file_name, [host])
        decompression.run()

        base_name = os.path.basename(file_name[:-3])
        dir_name = os.path.dirname(file_name[:-3])
        new_name = dir_name + "/data-" + base_name

        action = Remote("mv " + file_name[:-3] + " " + new_name, [host])
        action.run()
    elif file_name.endswith("zip"):
        decompression = Remote("unzip " + file_name, [host])
        decompression.run()

        base_name = os.path.basename(file_name[:-4])
        dir_name = os.path.dirname(file_name[:-4])
        new_name = dir_name + "/data-" + base_name

        action = Remote("mv " + file_name[:-4] + " " + new_name, [host])
        action.run()
    elif file_name.endswith("bz2"):
        decompression = Remote("bzip2 -d " + file_name, [host])
        decompression.run()

        base_name = os.path.basename(file_name[:-4])
        dir_name = os.path.dirname(file_name[:-4])
        new_name = dir_name + "/data-" + base_name

        action = Remote("mv " + file_name[:-4] + " " + new_name, [host])
        action.run()
    else:
        logger.warn("Unknown extension")
        return file_name

    return new_name


# Hosts #######################################################################

def generate_hosts(hosts_input):
    """Generate a list of hosts from the given file.

    Args:
      hosts_input: The path of the file containing the hosts to be used,
        or a comma separated list of site:job_id or an a comma separated list
        of hosts or an oargrid_job_id.
        If a file is used, each host should be in a different line.
        Repeated hosts are pruned.
        Hint: in a running Grid5000 job, $OAR_NODEFILE should be used.

    Return:
      list of Host: The list of hosts.
    """
    hosts = []
    if os.path.isfile(hosts_input):
        for line in open(hosts_input):
            h = Host(line.rstrip())
            if h not in hosts:
                hosts.append(h)
    elif ':' in hosts_input:
        # We assume the string is a comma separated list of site:job_id
        for job in hosts_input.split(','):
            site, job_id = job.split(':')
            hosts += get_oar_job_nodes(int(job_id), site)
    elif "," in hosts_input:
        # We assume the string is a comma separated list of hosts
        for hstr in hosts_input.split(','):
            h = Host(hstr.rstrip())
            if h not in hosts:
                hosts.append(h)
    elif hosts_input.isdigit():
        # If the file_name is a number, we assume this is a oargrid_job_id
        hosts = get_oargrid_job_nodes(int(hosts_input))
    else:
        # If not any of the previous, we assume is a single-host cluster where
        # the given input is the only host
        hosts = [Host(hosts_input.rstrip())]

    logger.debug('Hosts list: \n%s',
                 ' '.join(style.host(host.address.split('.')[0])
                          for host in hosts))
    return hosts


# Output formatting ###########################################################

class ColorDecorator(object):

    defaultColor = '\033[0;0m'

    def __init__(self, component, color):
        self.component = component
        self.color = color

    def __getattr__(self, attr):
        if attr == 'write' and self.component.isatty():
            return lambda x: self.component.write(self.color + x +
                                                  self.defaultColor)
        else:
            return getattr(self.component, attr)


# Configuration functions #####################################################

def create_xml_file(f):
    with open(f, "w") as fout:
        fout.write("<configuration>\n")
        fout.write("</configuration>")


def read_param_in_xml_file(f, name, default=None):
    tree = ET.parse(f)
    root = tree.getroot()
    res = root.findall("./property/[name='%s']/value" % name)
    if res:
        return res[0].text
    else:
        return default


def read_in_xml_file(f, param_names):

    if not param_names:
        return {}

    local_param_names = list(param_names)

    tree = ET.parse(f)
    root = tree.getroot()

    params = {}
    for name in local_param_names:
        res = root.findall("./property/[name='%s']/value" % name)
        # Warning multiple parameters?
        if res:
            params[name] = res[-1].text

    return params


def __replace_line(line, value):
    return re.sub(r'(.*)<value>[^<]*</value>(.*)', r'\g<1><value>' + value +
                  r'</value>\g<2>', line)


def replace_in_xml_file(f, name, value,
                        create_if_absent=False, replace_if_present=True):
    """Assign the given value to variable name in xml file f.

    Args:
      f (str):
        The path of the file.
      name (str):
        The name of the variable.
      value (str):
        The new value to be assigned:
      create_if_absent (bool, optional):
        If True, the variable will be created at the end of the file in case
        it was not already present.
      replace_if_present (bool, optional):
        If True, the current value will be replaced; otherwise, the current
        value will be maintained and the specified value ignored .

    Returns (bool):
      True if the assignment has been made, False otherwise.
    """

    current_value = read_param_in_xml_file(f, name)

    if current_value:
        if replace_if_present:
            (_, temp_file) = tempfile.mkstemp("", "xmlf-", "/tmp")
            with open(f) as in_file, open(temp_file, "w") as out_file:

                # Search property (we know it exists)
                line = in_file.readline()
                while "<name>" + name + "</name>" not in line:
                    out_file.write(line)
                    line = in_file.readline()

                # Replace with new value
                if "<value>" in line:
                    out_file.write(__replace_line(line, value))
                else:
                    out_file.write(line)
                    line = in_file.readline()
                    out_file.write(__replace_line(line, value))

                # changed = True

                # Write the rest of the file
                line = in_file.readline()
                while line != "":
                    out_file.write(line)
                    line = in_file.readline()

        else:
            return False
    else:
        if create_if_absent:
            (_, temp_file) = tempfile.mkstemp("", "xmlf-", "/tmp")
            with open(f) as in_file, open(temp_file, "w") as out_file:

                # Search end of file
                line = in_file.readline()
                while "</configuration>" not in line:
                    out_file.write(line)
                    line = in_file.readline()
                out_file.write("  <property><name>" + name + "</name>" +
                           "<value>" + str(value) + "</value></property>\n")
                out_file.write(line)
                # changed = True

        else:
            return False

    shutil.copyfile(temp_file, f)
    os.remove(temp_file)
    return True


def __parse_props_line(line):

    if line.startswith("#"):
        return None, None
    else:
        parts = line.split()
        if parts:
            return parts
        else:
            return None, None


def read_param_in_props_file(f, name, default=None):

    with open(f) as in_file:
        for line in in_file:
            (pname, pvalue) = __parse_props_line(line)
            if name and pname == name:
                return pvalue

    return default


def read_in_props_file(f, param_names=None):

    params = {}

    with open(f) as in_file:
        for line in in_file:
            (pname, pvalue) = __parse_props_line(line)
            if pname:
                if param_names is None or pname in param_names:
                    params[pname] = pvalue

    return params


def write_in_props_file(f, name, value, create_if_absent=False, override=True):

    current_value = read_param_in_props_file(f, name)

    if current_value:
        if override:
            (_, temp_file) = tempfile.mkstemp("", "xmlf-", "/tmp")
            with open(f) as in_file, open(temp_file, "w") as out_file:

                # Search property (we know it exists)
                line = in_file.readline()
                (pname, pvalue) = __parse_props_line(line)
                while not pname or pname != name:
                    out_file.write(line)
                    line = in_file.readline()
                    (pname, pvalue) = __parse_props_line(line)

                # Replace with new value
                out_file.write(pname + "\t" + str(value) + "\n")

                # Write the rest of the file
                line = in_file.readline()
                while line != "":
                    out_file.write(line)
                    line = in_file.readline()

            shutil.copyfile(temp_file, f)
            os.remove(temp_file)
            return True

        else:
            return False
    else:
        if create_if_absent:
            with open(f, "a") as out_file:
                out_file.write(name + "\t" + str(value) + "\n")
            return True

        else:
            return False
