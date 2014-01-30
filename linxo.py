# -*- coding: utf-8 -*-

from openerp.osv import osv
from openerp.osv import fields
from openerp.tools.translate import _
import time

class linxo_config_settings(osv.osv_memory):

    _name = 'linxo.config.settings'
    _inherit = 'res.config.settings'
    _columns = {
        'username': fields.char('Username (email address)', size=48),
        'password': fields.char('Password', size=48),
        'api_key': fields.char('Linxo API Key', size=48),
        'api_secret': fields.char('Linxo API Secret', size=60),
    }

class linxo_account(osv.osv):
    """ Bank Account stored on Linxo """
    _name = "linxo.account"
    _description = "Linxo Bank Account"
    _columns = {
        'name': fields.char('Account Name', size=120, required=True),
        'journal_id': fields.many2one('account.journal', 'OpenERP Journal Id', ondelete='cascade'),
        'account_group_name': fields.char('Account Group Name', size=30, required=True),
        'account_number': fields.char('Account Number', size=30, required=True),
        'type': fields.char('Account Type', size=30, required=True),
    }
    _sql_constraints = [
        ('name', 'unique(name)', 'The name of the bank account must be unique'),
        ('account_number', 'unique(account_number)', 'The account number must be unique')
    ]
    _order = 'name asc'


class linxo_transaction(osv.osv):
    """ Bank Transaction stored on Linxo """
    _name = 'linxo.transaction'
    _columns = {
        'account_id': fields.many2one('linxo.account', 'Linxo Account', ondelete='cascade'),
        'account_move_line_ids': fields.many2many(
            'account.move.line', 
            'linxo_transaction_account_move_line_rel',
            'account_move_line_id',
            'linxo_transaction_id',
            'Account Move Lines'),
        'amount': fields.float('Amount',digits=(12,3), required=True),
        'budget_date': fields.date('Budget Date', required=True),
        'date': fields.date('Date', required=True),
        'category' : fields.integer('Category'),
        'label': fields.char('Label', size=255, required=True),
        'notes': fields.char('Notes', size=255, required=True),
        'city' : fields.char('City', size=255),
        'original_label': fields.char('Original Label', size=255, required=True),
        'original_third_party': fields.char('Original Third Party', size=255, required=True),
    }

    _rec_name = 'label'
    _order = 'date asc'
