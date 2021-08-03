import requests
import json
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import confparser
from deepdiff import DeepDiff
import sys
import re

# Tower basic URLs		
TOWER_BASE_URL = 'https://10.1.150.50/api/v2/'
TOWER_ADMIN_USER = 'admin'
TOWER_ADMIN_PW = 'password'
TOWER_TEMPLATE_NAME = 'NetIM-Rollback-Job-Template'
#TOWER_TEMPLATE_NAME = 'Cisco-show-version'

# NetIM basic URLs	
NETIM_BASE_URL = 'https://10.1.150.232:8543/api/netim/v1/'
NETIM_URL = 'https://10.1.150.232:8543'
NETIM_ADMIN_USER = 'scadmin'
NETIM_ADMIN_PW = 'R3st0nVA'

# Function to fetch TOWER template JSON, based on the following curl:
# curl http://127.0.0.1:32117/api/v2/job_templates/?format=json -u "admin:HQnTrrxFdRYltxMohoxLB6rkOiUs6tvA"
def get_tower_template_ids(session):
	tower_template_url = TOWER_BASE_URL + 'job_templates/?format=json'
	headers = {"Accept": "application/json",
				"Content-Type": "application/json",
				"Connection": "keep-alive",
				"Cache-Control": "no-cache"
				}
	resp = session.get(tower_template_url, auth=(TOWER_ADMIN_USER,TOWER_ADMIN_PW), headers=headers, verify=False)
	all_template_dict = resp.json()['results']
	with open("all_template_id_dict.json", "w") as outfile: 
		json.dump(all_template_dict,outfile)
	print('GET template ID status code = '  + str(resp.status_code))
	# Look for hard coded TOWER template
	for each_template_dict in all_template_dict:
		if each_template_dict["name"] == TOWER_TEMPLATE_NAME:
			tower_template_id = each_template_dict["id"]
	return tower_template_id
    
# Function to execute the TOWER template ID, based on the following curl:
# curl -X POST http://127.0.0.1:32117/api/v2/job_templates/7/launch/ -u "admin:HQnTrrxFdRYltxMohoxLB6rkOiUs6tvA" -H "Content-Type: application/json" -d '{"extra_vars": {"survey_var": 7}}'
def execute_tower_template(tower_template_id, dev_name, device_archive_file_path, session):
	template_execute_url = TOWER_BASE_URL + 'job_templates/' + str(tower_template_id) + '/launch/'
	print(f'Temple url was {template_execute_url}')
	headers = {"Accept": "application/json",
				"Content-Type": "application/json",
				"Connection": "keep-alive",
				"Cache-Control": "no-cache"
				}

	# construct the body of the POST, this is the extra variable passed to Ansible (Ansible wording for variables passed to playbook)
	params = {"extra_vars": {"dev_name": dev_name, "device_archive_file_path": device_archive_file_path}}

	session = requests.session()
	resp = session.post(template_execute_url, auth=(TOWER_ADMIN_USER,TOWER_ADMIN_PW), headers=headers, data=json.dumps(params), verify=False)
	session.close()

	print(f'Response was {resp.text}')
	# Check status code
	if not resp.ok:
		resp.raise_for_status()
		print("Execute template: Error for template_id [%s] on TOWER:\n%s\n" % (tower_template_id, resp))
		return False
	else:
		print("Execute template: Successful request for template_id [%s] \"submitted\".\n" % str(tower_template_id))
	return True

# Function to fetch NetIM archive JSON based on the following URL:
# https://netim2.steeldemo.cloud:8543/api/netim/v1/devices/3980002/archives
def get_netim_archive_file_paths(dev_id,session):
	netim_archive_url = NETIM_BASE_URL + 'devices/' + dev_id + '/archives'
	headers = {"Accept": "application/json",
				"Content-Type": "application/json",
				"Connection": "keep-alive",
				"Cache-Control": "no-cache"
				}
	resp = session.get(netim_archive_url, auth=(NETIM_ADMIN_USER,NETIM_ADMIN_PW), headers=headers, verify=False)
	print(netim_archive_url)
	print('GET archive IDs status code = '  + str(resp.status_code))	
	# Now that we have the archives dictionary for the specific device, need to extract the specific file path
	# The NetIM dictionary represents the current config ("IMPORTED") and a set of older configs designated as "CLEAREED"
	# The first entry in the list after IMPORTED that says CLEARED is what I refer to as the roll-back config
	dev_archive_dict = resp.json()
	#print('**** device_archive_dict = ' + str(dev_archive_dict))
	archives_list = dev_archive_dict['items']
	first_cleared = False
	# Loop through the archived entries and stop when we find the first CLEARED
	for value_dict in archives_list:
		dev_id = value_dict['deviceId']
		print(value_dict['deviceId'])
		tags_list = value_dict['tags']
		print(value_dict['tags'])
		if 'IMPORTED' in tags_list:
			imported_device_archive_file_path = value_dict['links']['content']['path']
			print('The IMPORTED file path for device_id ' + dev_id + ' is ' + imported_device_archive_file_path)
		elif 'CLEARED' in tags_list:
			#print('**First CLEARED**')
			cleared_device_archive_file_path = value_dict['links']['content']['path']
			print('The CLEARED file path for device_id ' + dev_id + ' is ' + cleared_device_archive_file_path)
			first_cleared = True
			break
	return imported_device_archive_file_path, cleared_device_archive_file_path

#Function to fetch NetIM archive files (IMPORTED and first CLEARED)
## deviced_archive_file_path looks like this '/api/netim/v1/archives/281533/file'
def get_netim_archive_files(imported_device_archive_file_path,cleared_device_archive_file_path,session):
	netim_archive_url = NETIM_URL + imported_device_archive_file_path
	headers = {"Accept": "text/plain",
				"Content-Type": "text/plain",
				"Connection": "keep-alive",
				"Cache-Control": "no-cache"
				}
	resp = session.get(netim_archive_url, auth=(NETIM_ADMIN_USER,NETIM_ADMIN_PW), headers=headers, verify=False)
	#print(netim_archive_url)
	print('GET IMPORTED file path status code = '  + str(resp.status_code))
	
	# For debug, write the IMPORTED config to file
	imported_config_text = resp.text
	with open('imported_config.txt', mode='wb') as localfile:
		localfile.write(resp.content)
	
	# This is the confparser library from which we will use the ios.yml dissector
	# The dissector is a list of parent-child relationships that will be parsed
	dissector = confparser.Dissector.from_file('ios.yaml')
	# Using the resp.text from the REST call, convert the IMPORTED config text to JSON
	imported_config_json = (dissector.parse_str(imported_config_text))
	with open('imported_config.json','w') as outfile: 
		json.dump(imported_config_json,outfile)
	
	netim_archive_url = NETIM_URL + cleared_device_archive_file_path
	resp = session.get(netim_archive_url, auth=(NETIM_ADMIN_USER,NETIM_ADMIN_PW), headers=headers, verify=False)
	#print(netim_archive_url)
	print('GET CLEARED file path status code = '  + str(resp.status_code))	
	
	# For debug, write the CLEARED config to file
	cleared_config_text = resp.text
	with open('cleared_config.txt', mode='wb') as localfile:
		localfile.write(resp.content)

	# Using the resp.text from the REST call, convert the CLEARED config text to JSON
	cleared_config_json = (dissector.parse_str(cleared_config_text))
	with open('cleared_config.json','w') as outfile: 
		json.dump(cleared_config_json,outfile)
		
	# For NetIM retrieved files, need to convert to list
	cleared_config_list = cleared_config_text.split('\r\n')
	imported_config_list = imported_config_text.split('\r\n')

	return imported_config_list,cleared_config_list,imported_config_json,cleared_config_json

# This is a test function.  Does not require NetIM to grab configs.
# Just grab local config test files to stress test the parsing
def get_local_archive_files(imported_device_archive_file,cleared_device_archive_file):
	# This is the confparser library from which we will use the ios.yml dissector
	# The dissector is a list of parent-child relationships that will be parsed
	dissector = confparser.Dissector.from_file('ios.yaml')
	
	config_list_orig = open(imported_device_archive_file).readlines()
	imported_config_list = []
	for line in config_list_orig:
		line = line.rstrip()
		imported_config_list.append(line)
	# Using the imported test file, convert the IMPORTED config text to JSON
	imported_config_json = (dissector.parse_file(imported_device_archive_file))
	with open('imported_config.json','w') as outfile: 
		json.dump(imported_config_json,outfile)
	
	config_list_orig = open(cleared_device_archive_file).readlines()
	cleared_config_list = []
	for line in config_list_orig:
		line = line.rstrip()
		cleared_config_list.append(line)
	# Using the imported test file, convert the CLEARED config text to JSON
	cleared_config_json = (dissector.parse_file(cleared_device_archive_file))
	with open('cleared_config.json','w') as outfile: 
		json.dump(cleared_config_json,outfile)

	return imported_config_list,cleared_config_list,imported_config_json,cleared_config_json

# Function to extract config file sections, which will create the merge_config sections useable
# by napalm.  This includes a list with text to populate the Flask web page
def extract_sections(keyword_dict,section_change_list,cleared_config_file_list,imported_config_file_list):
	config_cleared_section = []
	config_imported_section = []
	# REMEMBER, cleared_config = ROLLBACK config = desired state
	# ddiff_json
	# {'dictionary_item_added': ['vlan 100'],
	#  'dictionary_item_removed': ['vlan 10', 'vlan 99'],
	#  'values_changed': ['interface GigabitEthernet0/2']}	

	# Starting in Rev 3, we group all difference categories into one section change list and the JSON simply becomes:
	# ['vlan 100','vlan 10', 'vlan 99','interface GigabitEthernet0/2']}	
	
	#The following is done for each section change:
	# parse the config section from *cleared config* and the imported config to show the change

	# The web page will display Imported and Cleared Config table
	# Each table row will have text representing the state of each config file for the specific changed section
	# web_page_diff_list = dict_row1, dict_row2, etc.
	# dict_row1 = {"cleared":cleared_text,"imported":imported_test} ** cleared = first column, imported = second columns of the table
	# web_page_diff_list, 
	# Each element of the list is a row first column and second column cell entry
	#Example format:
	# [{'cleared': 'no vlan 100\nno  name Barry-Test-VLAN\n!', 'imported': 'vlan 100\n name Barry-Test-VLAN\n!'}, {'cleared': 'vlan 10\n name Nuno-DC-Demo\n!', 'imported': ''}, {'cleared': 'vlan 99\n name SanFran_MPLS_Uplink\n!', 'imported': ''},]
	
	# From Rev 3, I to kept track of beginning and end lines for each section 
	# in both the cleared config file and imported config file, tag those with the section lines
	
	# But this is not used in Rev 4, since I went back to merge file commands versus complete recontruction of the config file
	# But to be safe, I left the start and end indexes of the changed sections in place (you never know..)
	web_page_diff_list = []
	# Now loop through each key_word detected as a change
	for key_value in section_change_list:
		print(key_value)
		key_word = key_value.split(' ')[0]
		key_index = keyword_dict[key_word]
		web_page_diff_dict = {}
		# table_cleared_string and table_imported_string will end up in web UI table
		table_cleared_string = ''
		table_imported_string = ''
		table_cleared_list = []
		table_imported_list = []
	
		found_section = False
		# First extract the section from the cleared config and mark line indexes for beginning and end of the section
		# These are stubs, I don't keep track of sections positions in the file anymore
		cleared_section_start = 0
		cleared_section_end = 0
		# This is just for 2 layer config like interface GigabitEthernet0/2
		if key_index == 1:
			for line in cleared_config_file_list:
				if key_value == line:
					found_section = True
					table_cleared_string = line + '<br/>'
				elif found_section == True and line != '!':
					table_cleared_string = table_cleared_string + line + '<br/>'
				elif found_section == True and line == '!':
					table_cleared_string = table_cleared_string + line
					found_section = False
		# And this is for single layer access-list 201 ...
		elif key_index == 0:
			for line in cleared_config_file_list:
				if key_value in line:
					table_cleared_string = table_cleared_string + '<br/>' + line

		# Next extract the section from the imported config (and mark line indexes for beginning and end of the section, Rev 4 for potential debug purposes..)
		imported_section_start = 0
		imported_section_end = 0
		# This is just for 2 layer config like interface
		if key_index == 1:
			for line in imported_config_file_list:
				if key_value == line:
					found_section = True
					table_imported_string = line + '<br/>'
				elif found_section == True and line != '!':
					table_imported_string = table_imported_string + line + '<br/>'
				elif found_section == True and line == '!':
					table_imported_string = table_imported_string + line
					found_section = False
		# And this is for single layer:
		elif key_index == 0:
			for line in imported_config_file_list:
				if key_value in line:
					table_imported_string = table_imported_string + '<br/>' + line

		# Place each text string and the line indexes in master change list (line indxexes in Rev 4 for potential debug purposes..)
		table_cleared_list = [table_cleared_string.lstrip('<br/>'),cleared_section_start,cleared_section_end]
		table_imported_list = [table_imported_string.lstrip('<br/>'),imported_section_start,imported_section_end]
		web_page_diff_dict["cleared"] = table_cleared_list
		web_page_diff_dict["imported"] = table_imported_list
		web_page_diff_list.append(web_page_diff_dict)
	return web_page_diff_list

# Function to parse the ddiff JSON output and created a section_change_list of any config section that changed (added, removed, or changed)
def ddiff_parse(keyword_dict,ddiff_json):
	section_change_list = []
	for ddiff_key in ddiff_json:
		ddiff_value = ddiff_json[ddiff_key]
		section_value_list = list(ddiff_value)
		for section_value_orig in section_value_list:
			# Format of the ddiff output for a root key, no methods to convert so hacked some parsing..
			# {"root['interface']['GigabitEthernet0/2']['access_vlan']": {'new_value': '153', 'old_value': '99'}}
			section_value_new = section_value_orig.replace(']','')
			section_key = section_value_new.split('[')[1].replace("'",'')
			section_value = section_key + ' ' + section_value_new.split('[')[2].replace("'",'')
			key_index = keyword_dict[section_key]
			# Only process allowable section values (vlan, interface, access_list, etc.)
			if section_key not in keyword_dict.keys():
				continue
			# for the single line config commands, need more of a descriptor
			# so section value needs to change to example access-list 201
			if key_index == 0:
				section_value = section_key + ' ' + section_value.split(' ')[1]
			# This is the master change list, again, we don't care if it appears in all categories of ddiff_json.  Only want single occurenece
			if section_value not in section_change_list:
				section_change_list.append(section_value)
	return section_change_list

# Function to create the napalm merge file
def create_config_merge_file(keyword_dict,selected_rows,web_page_diff_list):
	config_merge_file = open('config_merge_file.txt','w')
	for row in selected_rows:
		row_change_dict = web_page_diff_list[int(row)]
		cleared_change_dict = row_change_dict["cleared"]
		cleared_change_text = cleared_change_dict[0]
		cleared_change_text_split = cleared_change_text.split('<br/>')
		imported_change_dict = row_change_dict["imported"]
		imported_change_text = imported_change_dict[0]
		imported_change_text_split = imported_change_text.split('<br/>')
		########## THIS IS WORK IN PROGRESS, DEPENDS UPON LEVEL OF KEYWORDS ALLOWED ############
		########## NOT SURE IF I CAN GET AWAY WITH SINGLE LEVEL KEYWORDS WITH THIS STRUCTURE ###
		########## THE INTENT OF THE TEST FOR "" IS FOR SINGLE LEVEL SECTIONS LIKE VLAN 100  ###
		# If "cleared" == "", this means that the section was added, so needs to be deleted with "nos"
		# Specifically: read the imported section and place "no" in front of each line to delete the section
		if cleared_change_text == "":
			# Need to be concerned with levels:
			key = imported_change_text.split(' ')[0]
			key_index = keyword_dict[key]
			# interface section has more than 1 level, so index = 1
			if key_index == 1:
				print('HERE added for key_index = 1')
				for i in range(len(imported_change_text_split)-1):
					section_line = imported_change_text_split[i]
					# Don't "no" the interface keyword as an example
					if i==0:
						config_merge_file.write(section_line + '\n')
					# discovered that ! should not be present in the "no" commands
					elif section_line != '!':
						config_merge_file.write('no ' + section_line + '\n')
			elif key_index == 0:
				print('HERE added for key_index = 0')
				for section_line in imported_change_text_split:
					config_merge_file.write('no ' + section_line + '\n')

		# If "imported" == "", this means that the section was removed, so simply add the section back
		# Specifically: read the imported section and simply add these lines to the merged file
		elif imported_change_text == "":
			print('HERE removed')
			for section_line in cleared_change_text_split:
				config_merge_file.write(section_line + '\n')
				
		##### CURRENTLY TESTED WITH INTERFACE SECTIONS AND THIS WORKS, NOT SURE IF I NEED THE FIRST 2 IFs#####
		# Finally "Else" (neither imported or cleared are ""), then:
		# - Issue "nos" fo all values in the imported section (erase the section so to speak"
		# - add all the imported section lines right after the "nos"
		# - also I discovered that ! should not be present in the "no" commands
		else:
			print('HERE changed')
			# First handle the "no" removal of the imported sections
			for i in range(len(imported_change_text_split)):
				section_line = imported_change_text_split[i]
				# The first "if i == 0" works for interface structures, since you need to enter interface mode
				if i == 0:
					config_merge_file.write(section_line + '\n')
				elif i != len(imported_change_text_split) and section_line != '!':
					config_merge_file.write('no ' + section_line + '\n')
			# Now into the cleared section, add them back in
			# access-list remark will be removed without special logic
			# But remark is usually first and after applying "no access-list" to all above, access-list remark is removed
			# This is getting kludgy, but the code needs to detect "access-list remark", possibly others depending upon finally
			# supported keywords
			for i in range(0,len(cleared_change_text_split)):
				section_line = cleared_change_text_split[i]
				# Only rewrite access-list remark, not interface since the previous section already dropped us down one level
				if i == 0 and 'access-list remark' in section_line:
					config_merge_file.write(section_line + '\n')
				else:
					config_merge_file.write(section_line + '\n')
	config_merge_file.close()