from flask import Flask, render_template, request, jsonify, redirect, session, url_for, send_from_directory
import requests
import json
import confparser
from deepdiff import DeepDiff
import sys
import re
import os
import netim_rollback_utils as nru

app = Flask(__name__)

#http://10.1.1.1:5000/rollback?sysname=aternity-router.nyc.net&sysid=25

# The Flask servers operates in this order
# 1. The external link from NetIM will provide the device ID to Flask
# 2. Flask will reach back to NetIM and pull the config files (archive URL) and grab the rollback (last changed) config file ID
# 3. Flask then will retrieve the TOWER Template ID, then POST to the template ID to execute the template (passing the archive file ID in the POST)
# 4. NOT IN THIS SCRIPT.  TOWER then will use archive ID to retrieve the rollback config file from NetIM

# The newer version of the ansible_proxy script still handles full config file rollback, but now supports the selection of config file sections
# 1. The new external link from NetIM will provide the device ID to Flask
# 2. Flask will reach back to NetIM and pull the config files (archive URL) and *the entire rollback config file and imported file*.  Calling this cleared and imported in the script to align with NetIM nomenclature
# 3. Flask parses sections that it supports for selective rollbacks and then displays them on the web page.
# 4. The user selects the sections to rollback from the web page, clicks Submit and then the script creates a napalm merge file.  This just means only the portions of the config that need to change (including "no" Cisco commands to remove sections
# 5. Flask and Tower reside on the same host, so the merge file is accessible by Tower playbooks
# 6. Flask then will retrieve the Tower Template ID (same playbook for full rollback and section), then POST to the template ID to execute the template (passing the host name to rollback in the POST *and* variable to indicate this is section rollback (or full rollback))

# This is full rollback
@app.route('/rollback', methods=['GET'])
def rollback():
	dev_name = request.args.get('devname')
	print(dev_name)
	dev_id = request.args.get('devid')
	print(dev_id)
	# Now retrieve the archive IDs from NetIM, specifically the previous configuration which I call the rollback config file
	session = requests.session()
	# Only need cleared config for full rollback, but wanted one function for both full and section rollback
	imported_device_archive_file_path, device_archive_file_path = nru.get_netim_archive_file_paths(dev_id,session)
	session = requests.session()
	print('Cleared device archived file path is ' + device_archive_file_path)
	
	##### This is the real REST API approach, commented out for command line main.yml testing ####
	#tower_template_id = nru.get_tower_template_ids(session)
	#print('TOWER Template ID is ' + str(tower_template_id))
	## Need to also add full_rollback variable into the execute_tower_template function
	#nru.execute_tower_template(tower_template_id, dev_name, device_archive_file_path, session)
	
	##############   This is the Ansible command line solution to pass variables ################
	dev_file = open('dev_file.txt','w')
	dev_file.write(dev_id + '\n')
	dev_file.write(device_archive_file_path + '\n')
	# True means execute full rollback, False = merge (napalm variable)
	dev_file.write("'True'" + '\n')
	dev_file.close()
	ansible_rollback_cmd = 'ansible-playbook main-command-line.yml'
	print(ansible_rollback_cmd)
	os.system(ansible_rollback_cmd)
	return render_template('index_photo.html')

# This is section rollback external link from NetIM
@app.route('/rollback_section', methods=['GET','POST'])
def rollback_section():
	if request.method == 'GET':
		dev_name = request.args.get('devname')
		print(dev_name)
		dev_id = request.args.get('devid')
		print(dev_id)
		# For debug, hard code the cleared and imported files
		test_mode = 'False'
		imported_device_archive_file = 'gig2c_3r_4a_imported.txt'
		cleared_device_archive_file = 'gig2c_3a_4r_cleared.txt'
		# Retrive archives from NetIM
		if test_mode == 'False':
			session = requests.session()
			# Use the device ID to obtain the file paths for both the Imported and last Cleared archive file
			imported_device_archive_file_path, cleared_device_archive_file_path = nru.get_netim_archive_file_paths(dev_id,session)
			# Then use the imported and cleared file paths to retrieve the files
			imported_config_list,cleared_config_list,imported_config_json,cleared_config_json = nru.get_netim_archive_files(imported_device_archive_file_path,cleared_device_archive_file_path,session)
			session.close()

		# Retrieve files locally if test mode.  Only used for local CLI testing
		else:
			imported_config_list,cleared_config_list,imported_config_json,cleared_config_json = nru.get_local_archive_files(imported_device_archive_file,cleared_device_archive_file)

		# Now diff the JSONs using ddiff
		# Remember, cleared config is the ROLLBACK config (desired end state)
		# Possible outcomes of a comparison of imported config to cleared config:
		# 1) ADDED: Import config ADDED a config section NOT in cleared config
		# 2) DELETED: Import config DELETED a config section from the cleared config
		# 3) CHANGED: Import config CHANGED a config section from the cleared config
		ddiff_json = DeepDiff(cleared_config_json,imported_config_json,ignore_order=True)
		#print(ddiff_json)
		# Make sure there were any detected differences only per the allowable section words!!
		if ddiff_json == {}:
			print('## No allowable sections detected, allowable section keywords are: ##')
			print('interface, access-list')
			sys.exit()

		# And these are examples, what ddiff output would look with all cases covered:
		# {
		# 'dictionary_item_added': [root['vlan']['100']], 
		# 'dictionary_item_removed': [root['vlan']['10'], root['vlan']['99']], 
		# 'values_changed': {"root['interface']['GigabitEthernet0/2']['access_vlan']": {'new_value': '99', 'old_value': '153'}}
		# }
		# Translated to english,
		# "In the imported config file, vlan 100 was added"
		# "In the imported config file, vlans 10 and 99 were removed"
		# "In the imported config file, interface GigabitEthernet0/2 access_vlan is now 99 and was 153"

		# Big change in Rev 3.  Any difference logged in ddiff_json, "dictionary_item_added", "dictionary_item_removed", or "values_changed",
		# must be grouped into single change keyword.

		# Example:
		# interface gigEthernet 0/1
		#  description CHANGED_VALUE
		#  switchport mode REMOVED
		#  vlan ADDED

		# ddiff_json would track these as separate keys, but we need to treat them all the same so we can snip out entire section of the imported
		# file instead of all the complex logic required to add and remove ("no commands") the children

		# Need to define allowable section keywords and depth
		# In some cases, we need to config t then the first word:
		# example interface gigEthernet 0/2 always needs to be first, then "nos"
		# In other cases, just no the entire line
		# example: access-list 101 permit tcp 192.168.8.0 0.0.0.255 any eq 443
		#keyword_dict = {"vlan":"0","interface":"1"}
		keyword_dict = {"vlan":0,"interface":1,"access-list":0}
		with open('keyword_dict.json','w') as outfile: 
			json.dump(keyword_dict,outfile)

		# This list is the master "change" summary between cleared and imported configs
		# Note that this is still at the section keyword level (vlan, interface GigabitEthernet0/2, etc.)
		# Call the ddiff parse function to parse the ddiff_json and return the section_change_list
		section_change_list = nru.ddiff_parse(keyword_dict,ddiff_json)

		# Now that we have the section_change_list which is the master summary of all diffs,
		# call the extract sections function which will create the merge_config sections useable
		# by napalm.  This includes a dictionary with text to populate the Flask web page
		web_page_diff_list = nru.extract_sections(keyword_dict,section_change_list,cleared_config_list,imported_config_list)
		# I was not sure how to pass session variables from the GET route to the POST, so wrote out the small dict files and read in
		with open('web_page_diff_list.json','w') as outfile: 
			json.dump(web_page_diff_list,outfile)
		# This returns the table rows to index.html.
		return render_template('index.html', lines = web_page_diff_list)

	# This is the POST of the selected rows to roll-back from the Web page (user selects which sections to rollback)
	if request.method == 'POST':
		# I was not sure how to pass session variables from the GET route to the POST, so wrote out the small dict files and read in
		with open('web_page_diff_list.json') as json_file:
			web_page_diff_list = json.load(json_file)
		with open('keyword_dict.json') as json_file: 
			keyword_dict = json.load(json_file)
		submission_successful = False
		#print(request.form.getlist('checkbox'))
		selected_rows = request.form.getlist('checkbox')
		# Function called to created the merge file
		nru.create_config_merge_file(keyword_dict,selected_rows,web_page_diff_list)
		
		##### This is the real REST API approach, commented out for command line main.yml testing ####
		#tower_template_id = nru.get_tower_template_ids(session)
		#print('TOWER Template ID is ' + str(tower_template_id))
		## Need to also add full_rollback variable into the execute_tower_template function
		#nru.execute_tower_template(tower_template_id, dev_name, device_archive_file_path, session)

		# This is to test Ansible playbook for install merge, it will run on Tower, but for now run command line by Flask
		# In the real script, need to fetch the Template ID and then execute same way as full rollback (pass device name and rollback variable 
		# False in this case since True = full rollback, False = section rollback
		ansible_merge_cmd = 'ansible-playbook main-command-line.yml'
#		ansible_merge_cmd = 'ansible-playbook napalm_install_merge.yml -e config_file=\'config_merge_file.txt\''
		print(ansible_merge_cmd)
		os.system(ansible_merge_cmd)
		submission_successful = True
	return render_template('index.html', lines = web_page_diff_list, submission_successful=submission_successful)
	
if __name__ == "__main__":
#    app.run()
	app.run(host='0.0.0.0', port='5000',debug=True)