# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt

# metadata

'''
Load metadata (DocType) class

Example:

	meta = frappe.get_meta('User')
	if meta.has_field('first_name'):
		print "DocType" table has field "first_name"


'''

from __future__ import unicode_literals
import frappe, json
from frappe.utils import cstr, cint
from frappe.model import integer_docfield_properties, default_fields, no_value_fields, optional_fields
from frappe.model.document import Document
from frappe.model.base_document import BaseDocument
from frappe.model.db_schema import type_map
from frappe.modules import load_doctype_module
from frappe import _

def get_meta(doctype, cached=True):
	if cached:
		return frappe.cache().hget("meta", doctype, lambda: Meta(doctype))
	else:
		return Meta(doctype)

def get_table_columns(doctype):
	return frappe.cache().hget("table_columns", doctype,
		lambda: frappe.db.get_table_columns(doctype))

def load_doctype_from_file(doctype):
	fname = frappe.scrub(doctype)
	with open(frappe.get_app_path("frappe", "core", "doctype", fname, fname + ".json"), "r") as f:
		txt = json.loads(f.read())

	for d in txt.get("fields", []):
		d["doctype"] = "DocField"

	for d in txt.get("permissions", []):
		d["doctype"] = "DocPerm"

	txt["fields"] = [BaseDocument(d) for d in txt["fields"]]
	if "permissions" in txt:
		txt["permissions"] = [BaseDocument(d) for d in txt["permissions"]]

	return txt

class Meta(Document):
	_metaclass = True
	default_fields = list(default_fields)[1:]
	special_doctypes = ("DocField", "DocPerm", "Role", "DocType", "Module Def")

	def __init__(self, doctype):
		self._fields = {}
		if isinstance(doctype, Document):
			super(Meta, self).__init__(doctype.as_dict())
		else:
			super(Meta, self).__init__("DocType", doctype)
		self.process()

	def load_from_db(self):
		try:
			super(Meta, self).load_from_db()
		except frappe.DoesNotExistError:
			if self.doctype=="DocType" and self.name in self.special_doctypes:
				self.__dict__.update(load_doctype_from_file(self.name))
			else:
				raise

	def get_link_fields(self):
		return self.get("fields", {"fieldtype": "Link", "options":["!=", "[Select]"]})

	def get_dynamic_link_fields(self):
		if not hasattr(self, '_dynamic_link_fields'):
			self._dynamic_link_fields = self.get("fields", {"fieldtype": "Dynamic Link"})
		return self._dynamic_link_fields

	def get_select_fields(self):
		return self.get("fields", {"fieldtype": "Select", "options":["not in",
			["[Select]", "Loading..."]]})

	def get_table_fields(self):
		if not hasattr(self, "_table_fields"):
			if self.name!="DocType":
				self._table_fields = self.get('fields', {"fieldtype":"Table"})
			else:
				self._table_fields = doctype_table_fields

		return self._table_fields

	def get_valid_columns(self):
		if not hasattr(self, "_valid_columns"):
			if self.name in ("DocType", "DocField", "DocPerm", "Property Setter"):
				self._valid_columns = get_table_columns(self.name)
			else:
				self._valid_columns = self.default_fields + \
					[df.fieldname for df in self.get("fields") if df.fieldtype in type_map]

		return self._valid_columns

	def get_table_field_doctype(self, fieldname):
		return { "fields": "DocField", "permissions": "DocPerm"}.get(fieldname)

	def get_field(self, fieldname):
		'''Return docfield from meta'''
		if not self._fields:
			for f in self.get("fields"):
				self._fields[f.fieldname] = f

		return self._fields.get(fieldname)

	def has_field(self, fieldname):
		'''Returns True if fieldname exists'''
		return True if self.get_field(fieldname) else False

	def get_label(self, fieldname):
		'''Get label of the given fieldname'''
		df = self.get_field(fieldname)
		if df:
			label = df.label
		else:
			label = {
				'name': _('ID'),
				'owner': _('Created By'),
				'modified_by': _('Modified By'),
				'creation': _('Created On'),
				'modified': _('Last Modified On')
			}.get(fieldname) or _('No Label')
		return label

	def get_options(self, fieldname):
		return self.get_field(fieldname).options

	def get_link_doctype(self, fieldname):
		df = self.get_field(fieldname)

		if df.fieldtype == "Link":
			return df.options

		elif df.fieldtype == "Dynamic Link":
			return self.get_options(df.options)

		else:
			return None

	def get_search_fields(self):
		search_fields = self.search_fields or "name"
		search_fields = [d.strip() for d in search_fields.split(",")]
		if "name" not in search_fields:
			search_fields.append("name")

		return search_fields

	def get_fields_to_fetch(self, link_fieldname=None):
		'''Returns a list of docfield objects for fields whose values
		are to be fetched and updated for a particular link field

		These fields are of type Data, Link, Text, Readonly and their
		options property is set as `link_fieldname`.`source_fieldname`'''

		out = []

		if not link_fieldname:
			link_fields = [df.fieldname for df in self.get_link_fields()]

		for df in self.fields:
			if df.fieldtype in ('Data', 'Read Only', 'Text', 'Small Text',
				'Text Editor', 'Code') and df.options:
				if link_fieldname:
					if df.options.startswith(link_fieldname + '.'):
						out.append(df)
				else:
					if '.' in df.options:
						fieldname = df.options.split('.', 1)[0]
						if fieldname in link_fields:
							out.append(df)

		return out

	def get_list_fields(self):
		list_fields = ["name"] + [d.fieldname \
			for d in self.fields if (d.in_list_view and d.fieldtype in type_map)]
		if self.title_field and self.title_field not in list_fields:
			list_fields.append(self.title_field)
		return list_fields

	def get_custom_fields(self):
		return [d for d in self.fields if d.get('is_custom_field')]

	def get_title_field(self):
		return self.title_field or "name"

	def process(self):
		# don't process for special doctypes
		# prevent's circular dependency
		if self.name in self.special_doctypes:
			return

		self.add_custom_fields()
		self.apply_property_setters()
		self.sort_fields()
		self.get_valid_columns()

	def add_custom_fields(self):
		try:
			self.extend("fields", frappe.db.sql("""SELECT * FROM `tabCustom Field`
				WHERE dt = %s AND docstatus < 2""", (self.name,), as_dict=1,
				update={"is_custom_field": 1}))
		except Exception, e:
			if e.args[0]==1146:
				return
			else:
				raise

	def apply_property_setters(self):
		for ps in frappe.db.sql("""select * from `tabProperty Setter` where
			doc_type=%s""", (self.name,), as_dict=1):
			if ps.doctype_or_field=='DocType':
				if ps.property_type in ('Int', 'Check'):
					ps.value = cint(ps.value)

				self.set(ps.property, ps.value)
			else:
				docfield = self.get("fields", {"fieldname":ps.field_name}, limit=1)
				if docfield:
					docfield = docfield[0]
				else:
					continue

				if ps.property in integer_docfield_properties:
					ps.value = cint(ps.value)

				docfield.set(ps.property, ps.value)

	def sort_fields(self):
		"""sort on basis of insert_after"""
		custom_fields = sorted(self.get_custom_fields(), key=lambda df: df.idx)

		if custom_fields:
			newlist = []

			# if custom field is at top
			# insert_after is false
			for c in list(custom_fields):
				if not c.insert_after:
					newlist.append(c)
					custom_fields.pop(custom_fields.index(c))

			# standard fields
			newlist += [df for df in self.get('fields') if not df.get('is_custom_field')]

			newlist_fieldnames = [df.fieldname for df in newlist]
			for i in xrange(2):
				for df in list(custom_fields):
					if df.insert_after in newlist_fieldnames:
						cf = custom_fields.pop(custom_fields.index(df))
						idx = newlist_fieldnames.index(df.insert_after)
						newlist.insert(idx + 1, cf)
						newlist_fieldnames.insert(idx + 1, cf.fieldname)

				if not custom_fields:
					break

			# worst case, add remaining custom fields to last
			if custom_fields:
				newlist += custom_fields

			# renum idx
			for i, f in enumerate(newlist):
				f.idx = i + 1

			self.fields = newlist

	def get_fields_to_check_permissions(self, user_permission_doctypes):
		fields = self.get("fields", {
			"fieldtype":"Link",
			"parent": self.name,
			"ignore_user_permissions":("!=", 1),
			"options":("in", user_permission_doctypes)
		})

		if self.name in user_permission_doctypes:
			fields.append(frappe._dict({
				"label":"Name",
				"fieldname":"name",
				"options": self.name
			}))

		return fields

	def get_high_permlevel_fields(self):
		"""Build list of fields with high perm level and all the higher perm levels defined."""
		if not hasattr(self, "high_permlevel_fields"):
			self.high_permlevel_fields = []
			for df in self.fields:
				if df.permlevel > 0:
					self.high_permlevel_fields.append(df)

		return self.high_permlevel_fields

	def get_dashboard_data(self):
		'''Returns dashboard setup related to this doctype.

		This method will return the `data` property in the
		`[doctype]_dashboard.py` file in the doctype folder'''
		data = frappe._dict()
		try:
			module = load_doctype_module(self.name, suffix='_dashboard')
			if hasattr(module, 'get_data'):
				data = frappe._dict(module.get_data())
		except ImportError:
			pass

		return data

doctype_table_fields = [
	frappe._dict({"fieldname": "fields", "options": "DocField"}),
	frappe._dict({"fieldname": "permissions", "options": "DocPerm"})
]

#######

def is_single(doctype):
	try:
		return frappe.db.get_value("DocType", doctype, "issingle")
	except IndexError:
		raise Exception, 'Cannot determine whether %s is single' % doctype

def get_parent_dt(dt):
	parent_dt = frappe.db.sql("""select parent from tabDocField
		where fieldtype="Table" and options=%s and (parent not like "old_parent:%%")
		limit 1""", dt)
	return parent_dt and parent_dt[0][0] or ''

def set_fieldname(field_id, fieldname):
	frappe.db.set_value('DocField', field_id, 'fieldname', fieldname)

def get_field_currency(df, doc=None):
	"""get currency based on DocField options and fieldvalue in doc"""
	currency = None

	if not df.get("options"):
		return None

	if not doc:
		return None

	if not getattr(frappe.local, "field_currency", None):
		frappe.local.field_currency = frappe._dict()

	if not (frappe.local.field_currency.get((doc.doctype, doc.name), {}).get(df.fieldname) or
		(doc.parent and frappe.local.field_currency.get((doc.doctype, doc.parent), {}).get(df.fieldname))):

		ref_docname = doc.parent or doc.name

		if ":" in cstr(df.get("options")):
			split_opts = df.get("options").split(":")
			if len(split_opts)==3:
				currency = frappe.db.get_value(split_opts[0], doc.get(split_opts[1]), split_opts[2])
		else:
			currency = doc.get(df.get("options"))
			if doc.parent:
				if currency:
					ref_docname = doc.name
				else:
					currency = frappe.db.get_value(doc.parenttype, doc.parent, df.get("options"))

		if currency:
			frappe.local.field_currency.setdefault((doc.doctype, ref_docname), frappe._dict())\
				.setdefault(df.fieldname, currency)

	return frappe.local.field_currency.get((doc.doctype, doc.name), {}).get(df.fieldname) or \
		(doc.parent and frappe.local.field_currency.get((doc.doctype, doc.parent), {}).get(df.fieldname))

def get_field_precision(df, doc=None, currency=None):
	"""get precision based on DocField options and fieldvalue in doc"""
	from frappe.utils import get_number_format_info

	if cint(df.precision):
		precision = cint(df.precision)

	elif df.fieldtype == "Currency":
		number_format = None
		if not currency and doc:
			currency = get_field_currency(df, doc)

		if not currency:
			# use default currency
			currency = frappe.db.get_default("currency")

		if currency:
			number_format = frappe.db.get_value("Currency", currency, "number_format", cache=True)

		if not number_format:
			number_format = frappe.db.get_default("number_format") or "#,###.##"

		decimal_str, comma_str, precision = get_number_format_info(number_format)

	else:
		precision = cint(frappe.db.get_default("float_precision")) or 3

	return precision


def get_default_df(fieldname):
	if fieldname in default_fields:
		if fieldname in ("creation", "modified"):
			return frappe._dict(
				fieldname = fieldname,
				fieldtype = "Datetime"
			)

		else:
			return frappe._dict(
				fieldname = fieldname,
				fieldtype = "Data"
			)

def trim_tables():
	"""Use this to remove columns that don't exist in meta"""
	ignore_fields = default_fields + optional_fields

	for doctype in frappe.db.get_all("DocType", filters={"issingle": 0}):
		doctype = doctype.name
		columns = frappe.db.get_table_columns(doctype)
		fields = [df.fieldname for df in frappe.get_meta(doctype).fields if df.fieldtype not in no_value_fields]
		columns_to_remove = [f for f in list(set(columns) - set(fields)) if f not in ignore_fields
			and not f.startswith("_")]
		if columns_to_remove:
			print doctype, "columns removed:", columns_to_remove
			columns_to_remove = ", ".join(["drop `{0}`".format(c) for c in columns_to_remove])
			query = """alter table `tab{doctype}` {columns}""".format(
				doctype=doctype, columns=columns_to_remove)
			frappe.db.sql_ddl(query)

def clear_cache(doctype=None):
	cache = frappe.cache()

	for key in ('is_table', 'doctype_modules'):
		cache.delete_value(key)

	groups = ["meta", "form_meta", "table_columns", "last_modified",
		"linked_doctypes", 'email_alerts']

	def clear_single(dt):
		for name in groups:
			cache.hdel(name, dt)

	if doctype:
		clear_single(doctype)

		# clear all parent doctypes
		for dt in frappe.db.sql("""select parent from tabDocField
			where fieldtype="Table" and options=%s""", (doctype,)):
			clear_single(dt[0])

		# clear all notifications
		from frappe.desk.notifications import delete_notification_count_for
		delete_notification_count_for(doctype)

	else:
		# clear all
		for name in groups:
			cache.delete_value(name)
