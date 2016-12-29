# -*- coding: utf-8 -*-
# © 2016 Daniel Reis
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from openerp import models, fields, api
from openerp import SUPERUSER_ID
from openerp import tools
from lxml import etree

# Extended name search is only used on some operators
ALLOWED_OPS = set(['ilike', 'like'])


@tools.ormcache(skiparg=0)
def _get_rec_names(self):
    "List of fields to search into"
    model = self.env['ir.model'].search(
        [('model', '=', str(self._model))])
    rec_name = [self._rec_name] or []
    other_names = model.name_search_ids.mapped('name')
    return rec_name + other_names


@tools.ormcache(skiparg=0)
def _get_rec_exact_names(self):
    "List of fields to exact search into"
    model = self.env['ir.model'].search(
        [('model', '=', str(self._model))])
    other_names = model.name_search_exact_ids.mapped('name')
    return other_names


@tools.ormcache(skiparg=0)
def _get_add_smart_search(self):
    "Add Smart Search on search views"
    return self.env['ir.model'].search(
        [('model', '=', str(self._model))]).add_smart_search


@tools.ormcache(skiparg=0)
def _get_separator(self):
    return self.env['ir.config_parameter'].get_param(
        'base_name_search_improved.separator', default=" ")


def _extend_name_results(self, domain, results, limit):
    result_count = len(results)
    if result_count < limit:
        domain += [('id', 'not in', [x[0] for x in results])]
        recs = self.search(domain, limit=limit - result_count)
        results.extend(recs.name_get())
    return results


class ResPartner(models.Model):
    _inherit = 'res.partner'

    smart_search = fields.Char(
        compute='_compute_smart_search',
        search='_search_smart_search')

    @api.multi
    def _compute_smart_search(self):
        return False

    @api.model
    def fields_view_get(
            self, view_id=None, view_type=False, toolbar=False, submenu=False):
        res = super(ResPartner, self).fields_view_get(
            view_id=view_id, view_type=view_type, toolbar=toolbar,
            submenu=submenu)
        if view_type == 'search' and _get_add_smart_search(self):
            eview = etree.fromstring(res['arch'])
            placeholders = eview.xpath("//search/field")
            if placeholders:
                placeholder = placeholders[0]
            else:
                placeholder = eview.xpath("//search")[0]
            placeholder.addnext(
                etree.Element('field', {'name': 'smart_search'}))
            eview.remove(placeholder)
            res['arch'] = etree.tostring(eview)
            res['fields'].update(self.fields_get(['smart_search']))
        return res

    @api.model
    def _search_smart_search(self, operator, value):
        enabled = self.env.context.get('name_search_extended', True)
        name = value
        if name and enabled and operator in ALLOWED_OPS:
            exact_fields_names = _get_rec_exact_names(self)
            for rec_name in exact_fields_names:
                recs = self.search([(rec_name, '=ilike', name)])
                if recs:
                    return [(rec_name, '=ilike', name)]

            all_names = _get_rec_names(self)
            domain = []
            for word in name.split(_get_separator(self)):
                word_domain = []
                for rec_name in all_names:
                    word_domain = (
                        word_domain and ['|'] + word_domain or
                        word_domain
                    ) + [(rec_name, operator, word)]
                domain = (
                    domain and ['&'] + domain or domain
                ) + word_domain
            return domain
        return []


class ModelExtended(models.Model):
    _inherit = 'ir.model'

    add_smart_search = fields.Boolean(
        help="Add Smart Search on search views"
    )
    name_search_ids = fields.Many2many(
        'ir.model.fields',
        string='Name Search Fields')
    name_search_exact_ids = fields.Many2many(
        'ir.model.fields',
        'ir_model_name_search_exact_rel',
        'model_id', 'field_id',
        string='Name Search Exact Fields',
        help="If we found exact matches for this fields then we return only "
        "this results and we don't keep going")

    def _register_hook(self, cr, ids=None):

        def make_name_search():

            @api.model
            def name_search(self, name='', args=None,
                            operator='ilike', limit=100):
                enabled = self.env.context.get('name_search_extended', True)

                # first we search for an exact match, if we found any, we
                # return it
                if name and enabled and operator in ALLOWED_OPS:
                    exact_fields_names = _get_rec_exact_names(self)
                    for rec_name in exact_fields_names:
                        recs = self.search([(rec_name, '=ilike', name)])
                        if recs:
                            return recs.name_get()

                # Perform standard name search
                res = name_search.origin(
                    self, name=name, args=args, operator=operator, limit=limit)
                # Perform extended name search
                # Note: Empty name causes error on
                #       Customer->More->Portal Access Management
                if name and enabled and operator in ALLOWED_OPS:
                    # Support a list of fields to search on
                    all_names = _get_rec_names(self)
                    base_domain = args or []
                    # Try regular search on each additional search field
                    for rec_name in all_names[1:]:
                        domain = [(rec_name, operator, name)]
                        res = _extend_name_results(
                            self, base_domain + domain, res, limit)
                    # Try ordered word search on each of the search fields
                    for rec_name in all_names:
                        domain = [(rec_name, operator, name.replace(' ', '%'))]
                        res = _extend_name_results(
                            self, base_domain + domain, res, limit)

                    # we change this one for the next one
                    # Try unordered word search on each of the search fields
                    # for rec_name in all_names:
                    #     domain = [(rec_name, operator, x)
                    #               for x in name.split() if x]
                    #     res = _extend_name_results(
                    #         self, base_domain + domain, res, limit)

                    # Try unordered word search on each of the search fields
                    # we only perform this search if we have at least one
                    # split character
                    separator = _get_separator(self)
                    if separator in name:
                        domain = []
                        for word in name.split(separator):
                            word_domain = []
                            for rec_name in all_names:
                                word_domain = (
                                    word_domain and ['|'] + word_domain or
                                    word_domain
                                ) + [(rec_name, operator, word)]
                            domain = (
                                domain and ['&'] + domain or domain
                            ) + word_domain
                        res = _extend_name_results(
                            self, base_domain + domain, res, limit)

                return res
            return name_search

        if ids is None:
            ids = self.search(cr, SUPERUSER_ID, [])
        for model in self.browse(cr, SUPERUSER_ID, ids):
            Model = self.pool.get(model.model)
            if Model:
                Model._patch_method('name_search', make_name_search())
        return super(ModelExtended, self)._register_hook(cr)
