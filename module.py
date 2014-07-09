# -*- coding: utf-8 -*-

from lxml import etree
import logging
import time
import json
import re
import datetime
import requests

from openerp import pooler, tools
from openerp import SUPERUSER_ID
from openerp.osv import osv
from openerp.osv import fields
from openerp.tools.translate import _
from openerp.addons import account
import openerp.addons.decimal_precision as dp

_logger = logging.getLogger(__name__)

def has_changed(local_value, linxo_value, data_type=None):
    """Perform comparaison according to type

    Parameters
    linxo_value comes from linxo_object : always str
    local_value comes from database
    """
    test_value = format_linxo_data(linxo_value, data_type)
    if data_type == 'date':
        # Openerp treat date as str ...
        return local_value != str(test_value)
    return local_value != test_value

def format_linxo_data(linxo_value, data_type=None):
    """see has_changed ...
    """
    if data_type is None:
        return linxo_value
    elif data_type == 'int':
        return int(linxo_value)
    elif data_type == 'float':
        return float(linxo_value)
    elif data_type == 'date':
        from datetime import date
        return date.fromtimestamp(int(linxo_value))
    else:
        raise Exception('wrong data_type')


def check_changes(local, linxo, translation_dict, data_type=None):
    """Generic function to call when updating object
    """
    changes = None

    for local_key, linxo_key in translation_dict.iteritems():
        if local_key in local._columns and  linxo_key in linxo:
            value = getattr(local, local_key)
            if has_changed(value, linxo[linxo_key], data_type):
                new_value = format_linxo_data(linxo[linxo_key])
                setattr(local, local_key, new_value)
                if changes is None:
                    _logger.debug('key {} differs {} {}'.format(local_key, value, linxo[linxo_key]))
                    return True
    return changes

def _get_headers():
    """Define header to use in requests
    """
    return { 'X-LINXO-API-Version' : '1.4',
              'Content-Type' : 'application/json' }

class APIError(Exception):
    """Exception that handle return format of the linxo API
    """

    def __init__(self, result):
        self.result = result
        super(APIError, self).__init__()

    def __str__(self):
        return '%s errorMessage: %s failedAction: %s' % (
            self.result['errorCode'],
            self.result['errorMessage'], 
            self.result['failedAction'])

class linxo_sync(osv.osv_memory):
    _name = 'linxo.sync'

    _columns = {
        'name': fields.char('Name', size=48),
        'account_treated': fields.integer('Number of account treated'),
        'account_updated': fields.integer('Number of account updated'),
        'account_created': fields.integer('Number of new account'),
        'transaction_treated': fields.integer('Number of transaction treated'),
        'transaction_updated': fields.integer('Number of transaction updated'),
        'transaction_created': fields.integer('Number of new transaction'),
    }

    _defaults = {
        'account_treated': 0,
        'account_updated': 0,
        'account_created': 0,
        'transaction_treated': 0,
        'transaction_updated': 0,
        'transaction_created': 0,
    }


    def do_reconciliation(self, cr, uid, ids, context=None):
        transaction_obj = self.pool.get('linxo.transaction')
        search_args = [('account_move_line_id', '=', None)]
        transactions_ids = transaction_obj.search(cr, uid, search_args, context=context)
        if transactions_ids:
            transaction_obj.search_reconciliation(cr, uid, transactions_ids, context=context)
            return True
        else:
            return False

    def do_sync(self, cr, uid, ids, context=None):
        """Perform sync with openerp server"""

        if not context:
            context={}

        # batch context key will be use not to raise exeption
        context['batch'] = True

        # Move this to overrided __init__ ?
        self.base_domain = 'wwws.linxo.com'
        self.verify_ssl = True

        self.url = 'https://%s/json' % self.base_domain
        self.logged_in = False

        ir_values = self.pool.get('ir.values')

        for param_name in ['api_secret', 'api_key', 'username', 'password']:
            value = ir_values.get_default(cr, uid, 'linxo.config', param_name)
            setattr(self, param_name, value)

        self.session = requests.Session()

        # Generate nonce 
        import random
        self.nonce = '%030x' % random.randrange(16**30)

        # Set cookies
        self._set_cookies()

        # Set headers
        self.session.headers.update(_get_headers())

        # Statistics
        self.account_treated = 0
        self.account_updated = 0
        self.account_created = 0
        self.transaction_treated = 0
        self.transaction_updated = 0
        self.transaction_created = 0

        # First pass, create or update bankAccount in our database
        bank_accounts = self._get_bank_accounts()
        for account_type in ['Checkings']:
            for account in bank_accounts['accountsByType'][account_type]:
                result = self._handle_bank_account(cr, uid, ids, context, account)
                if result == 1:
                    self.account_created += 1
                elif result == 2:
                    self.account_updated += 1
                self.account_treated += 1

        # Stop here : init sync
        if 'account_only' in context:
            return self

        # Second pass, fetch operation from bankAccount
        for account_type in ['Checkings']:
            for account in bank_accounts['accountsByType'][account_type]:
                counter = 0
                num_rows = 100
                transactions = self._get_transactions(account=account, start_row=num_rows * counter, num_rows=num_rows)
                real_data = transactions['transactions']

                # We might need to fetch more
                fetch_more = self._need_to_fetch_more(cr, uid, ids, context, transactions, counter, num_rows)
                while fetch_more:
                    # Fetch next
                    counter += 1
                    temp_transactions = self._get_transactions(account=account, start_row=num_rows * counter, num_rows=num_rows)
                    temp_data = temp_transactions['transactions']

                    # Merge data
                    real_data.extend(temp_data)

                    # Do we need to fethc more ?
                    fetch_more = self._need_to_fetch_more(cr, uid, ids, context, temp_transactions, counter, num_rows)

                _logger.info('We fetched %d transactions' % len(real_data))

                for transaction in transactions['transactions']:
                    self._handle_bank_transaction(cr, uid, ids, context, transaction)

                    if result == 1:
                        self.transaction_created += 1
                    elif result == 2:
                        self.transaction_updated += 1
                    self.transaction_treated += 1

        data = {}
        for key in self._defaults:
            data[key] = getattr(self, key)

        self.pool.get('linxo.sync').write(cr, uid, ids, data, context=context)
        return self

    def _need_to_fetch_more(self, cr, uid, ids, context,  transactions, counter, num_rows):
        """Check if the lowest id according to the account is in database
        If not, we need to check more rows
        """

        max_number = counter * num_rows + num_rows # round 0 : 100 data

        # Already fetch enought data
        if max_number > transactions['totalCount']:
            _logger.debug('We fetched more than the max, stopping here')
            return False

        # Look in database lowest id (older transaction)
        sorted_transactions = sorted(transactions['transactions'], key=lambda transaction: transaction['id'])
        lowest_id = int(sorted_transactions[0]['id'])
        _logger.debug('lowest_id so far is %d' % lowest_id)

        # Do we have a linxo.transaction with that id ?
        transaction_obj = self.pool.get('linxo.transaction')
        transactions_ids = transaction_obj.search(cr, uid, [('linxo_id', '=', lowest_id)], context=context)

        if transactions_ids:
            return False
        else:
            return True


    def _handle_bank_transaction(self, cr, uid, ids, context, transaction):
        """Replicate linxo transaction in our local database

        Take the description of linxo and check if the account exist in database.
        Update if necessary the information

        Return 
        0 if no changes
        1 if created
        2 if updated
        """
        _logger.debug('handling transaction linxo_id=%s' % transaction['id'])

        transaction_obj = self.pool.get('linxo.transaction')
        transaction_ids = transaction_obj.search(cr, uid, [('linxo_id', '=', transaction['id'])], context=context)

        # Need to fetch associated account
        account_obj = self.pool.get('linxo.account')
        account_ids = account_obj.search(cr, uid, [('linxo_id', '=', transaction['bankAccountId'])], context=context)
        if not account_ids:
            raise Exception('WTF ? We should have an account here')
        local_account = account_obj.browse(cr, uid, account_ids[0], context=context)

        def _get_translation_dict(data=None):
            if data is None:
                return {
                    'amount': 'amount',
                    'label': 'label',
                    'notes': 'notes',
                    #'original_city': 'originalCity',
                    'original_label': 'originalLabel',
                    'original_third_party': 'originalThirdParty',}
            elif data is 'int':
                return {
                    #'original_category': 'originalCategory',
                    #'category_id': 'categoryId',
                    'linxo_id' : 'id', }
            if data is 'float':
                return {
                    'amount': 'amount',}
            elif data == 'date':
                return {
                    'budget_date': 'budgetDate',
                    'date': 'date',
                    #'original_date_available': 'originalDateAvailable',
                    #'original_date_initiated': 'originalDateInitiated',
                }
            else:
                raise Exception('wrong data {}'.format(data))

        def _get_data_types():
            """Return data_type use to translation linxo data to database
            """
            return [None, 'int', 'float', 'date']

        def _get_values(account):
            """Return an hash with value to update in erp database
            """
            transaction_values = { 'account_id' : account.id }
            for data_type in _get_data_types():
                for local_key, linxo_key in _get_translation_dict(data_type).iteritems():
                    if linxo_key in transaction:
                        value = format_linxo_data(transaction[linxo_key], data_type)
                        transaction_values[local_key] = value
            return transaction_values

        if transaction_ids:
            local_transaction = transaction_obj.browse(cr, uid, transaction_ids[0], context=context)


            to_return = 0
            for data_type in _get_data_types():
                temp_changes = check_changes(local_transaction, transaction, _get_translation_dict(data_type), data_type)
                if temp_changes:
                    transaction_values = _get_values(local_account)
                    transaction_obj.write(cr, uid, transaction_ids, transaction_values, context=context)
                    _logger.debug('updated transaction %d' % transaction_ids[0])
                    to_return = 2
                    break
            _logger.debug('nothing to do for transaction %d' % transaction_ids[0])

            if not local_transaction.account_move_line_id:
                transaction_obj.search_reconciliation(cr, uid, [transaction_ids[0]], context=context)

            return to_return

        else:
            transaction_values = _get_values(local_account)
            tr_id = transaction_obj.create(cr, uid, transaction_values, context=context)
            _logger.debug('created new transaction %d' % tr_id)
            transaction_obj.search_reconciliation(cr, uid, [tr_id], context=context)
            return 1


    def _handle_bank_account(self, cr, uid, ids, context, account):
        """Replicate linxo bankAccount to our local database

        Take the description of linxo and check if the account exist in database.
        Update if necessary the information

        Return 
        0 if no changes
        1 if created
        2 if updated
        """
        _logger.debug('handling account linxo_id %s' % account['id'])

        account_obj = self.pool.get('linxo.account')
        account_ids = account_obj.search(cr, uid, [('linxo_id', '=', account['id'])], context=context)

        def _get_translation_dict(data=None):
            """Link between our object name and linxo object
            """
            if data is None:
                return {
                    'account_group_name' : 'accountGroupName',
                    'account_number': 'accountNumber',
                    'name': 'name',
                    'type': 'type' }
            elif data is 'int':
                return {
                    'linxo_id' : 'id' }
            else:
                raise Exception('wrong data {}'.format(data))

        def _get_data_types():
            """Return data_type use to translation linxo data to database
            """
            return [None, 'int']

        def _get_values():
            account_values = {}
            for data_type in _get_data_types():
                for local_key, linxo_key in _get_translation_dict(data_type).iteritems():
                    if linxo_key in account:
                        value = format_linxo_data(account[linxo_key], data_type)
                        account_values[local_key] = value
            return account_values


        if account_ids:
            local_account = account_obj.browse(cr, uid, account_ids[0], context=context)

            changes = None
            for data_type in _get_data_types():
                temp_changes = check_changes(local_account, account, _get_translation_dict(data_type), data_type)
                if temp_changes:
                    account_values = _get_values()
                    account_obj.write(cr, uid, account_ids, account_values, context=context)
                    _logger.debug('updated account %d' % account_ids[0])
                    return 2
            _logger.debug('nothing to do for account %d' % account_ids[0])
            return 0
        else:
            account_values = _get_values()
            account_id = account_obj.create(cr, uid, account_values, context=context)
            _logger.debug('created new account %d' % account_id)
            return 1

    def _get_bank_accounts(self):
        """Fetch list of bank account on linxo
        """
        payload = {
            'actionName' : 'com.linxo.gwt.rpc.client.pfm.GetBankAccountListAction',
            'action' : {
                'includeClosed' : False,
            }
        }

        if not self.logged_in:
            self._login()

        return self._perform_query(payload)

    def _get_transactions(self, *args, **kwargs):
        """Fetch latest operation on linxo, specific to one account
        """

        if 'account' in kwargs:
            account = kwargs['account']
        else:
            raise MissingParameter('account')

        if 'start_row' in kwargs:
            start_row = kwargs['start_row']
        else:
            start_row = 0

        if 'num_rows' in kwargs:
            num_rows = kwargs['num_rows']
        else:
            num_rows = 100

        _logger.debug('Going to fetch %s transaction for account %s starting at %s' % (
            num_rows, account['id'], start_row))

        payload = {
            'actionName' : 'com.linxo.gwt.rpc.client.pfm.GetTransactionsAction',
            'action' : {
                'accountType' : account['type'],
                'accountId' : account['id'],
                'labels' : [],
                'categoryId' : None,
                'tagId' : None,
                'startRow' : start_row,
                'numRows' : num_rows,
            }
        }

        return self._perform_query(payload)

    def _login(self):
        """Perform authentification on linxo, using secureData extracted data
        """
        payload = {
            'actionName' : 'com.linxo.gwt.rpc.client.auth.LoginAction',
            'action' : {
                'email'    : self.username,
                'password' : self.password,
            },
        }

        self._perform_query(payload)
        self.logged_in = True

    def _logout(self):
        """Perform logout, called by __del__
        """
        payload = { 
            'actionName' : 'com.linxo.gwt.rpc.client.auth.LogoutAction' }
        self._perform_query(payload)

    def _set_cookies(self):
        """Fetch cookies from auth page
        """
        auth_page = 'https://%s/auth.page' % self.base_domain
        self.session.get(auth_page, verify=self.verify_ssl)


    def _get_hash(self):
        """Low level function that generate hash needed for linxo security
        """

        import time
        import base64
        import hashlib
        timestamp = int(time.time())
        sha1 = hashlib.sha1("%s%s%s" % (self.nonce, timestamp, self.api_secret))
        signature = base64.b64encode(sha1.hexdigest())

        return {
            'nonce'     : self.nonce,
            'timeStamp' : timestamp,
            'apiKey'    : self.api_key,
            'signature' : signature
        }



    def _perform_query(self, payload):
        """Low level function that does the get action on linxo
        """

        # No action in payload yell
        if 'actionName' not in payload:
            raise Exception('Missing key actionName is payload')

        # If no hash, add it
        if 'hash' not in payload:
            payload['hash'] = self._get_hash()

        # If no secret, add it
        if 'action' not in payload:
            payload['action'] = {}

        if 'secret' not in payload['action']:
            payload['action']['secret'] = self.session.cookies['LinxoSession']

        result = self.session.post(self.url, 
                                   data=json.dumps(payload), 
                                   verify=self.verify_ssl)

        # Now r.text should contain )]}'\n , remove that and jsonize
        raw_json = re.compile('\)\]\}\'\n').sub('', result.text)

        # Result are check according to functions called
        json_response = json.loads(raw_json)

        if payload['actionName'] == 'com.linxo.gwt.rpc.client.auth.LoginAction':
            # Special treatment for login:
            if 'result' not in json_response:
                raise osv.except_osv(_("Error!"), _("Weird loggin error ..."))
            elif 'userId' not in json_response['result']:
                blocked = json_response['result']['blocked']
                if blocked:
                    raise osv.except_osv(_("Error!"), _("Too much login tentative, you are now blocked."))
                else:
                    raise osv.except_osv(_("Warning"), _("Unable to login : wrong credentials."))
        elif json_response['resultName'] == 'com.linxo.gwt.server.support.json.ErrorResult':
            raise APIError(json_response['result'])

        return json_response['result']


    def __del__(self):
        if self.logged_in:
            self._logout()
        super(self.__class__, self).__del__() 


linxo_sync()
    


class linxo_config_settings(osv.osv_memory):
    _name = 'linxo.config.settings'
    _inherit = 'res.config.settings'
    _columns = {
        'username': fields.char('Username (email address)', size=48),
        'password': fields.char('Password', size=48),
        'api_key': fields.char('Linxo API Key', size=48),
        'api_secret': fields.char('Linxo API Secret', size=60),
    }

    def get_default_username(self, cr, uid, ids, context=None):
        """Get default value if already defined"""
        return self._get_default(cr, uid, ids, context, 'username')

    def set_default_username(self, cr, uid, ids, context=None):
        return self._set_default(cr, uid, ids, context, 'username')

    def get_default_password(self, cr, uid, ids, context=None):
        """Get default value if already defined"""
        return self._get_default(cr, uid, ids, context, 'password')

    def set_default_password(self, cr, uid, ids, context=None):
        return self._set_default(cr, uid, ids, context, 'password')

    def get_default_api_key(self, cr, uid, ids, context=None):
        """Get default value if already defined"""
        return self._get_default(cr, uid, ids, context, 'api_key')

    def set_default_api_key(self, cr, uid, ids, context=None):
        return self._set_default(cr, uid, ids, context, 'api_key')

    def get_default_api_secret(self, cr, uid, ids, context=None):
        """Get default value if already defined"""
        return self._get_default(cr, uid, ids, context, 'api_secret')

    def set_default_api_secret(self, cr, uid, ids, context=None):
        return self._set_default(cr, uid, ids, context, 'api_secret')

    def _get_default(self, cr, uid, ids, context, param_name):
        """Get default value if already defined"""
        ir_values = self.pool.get('ir.values')
        value = ir_values.get_default(cr, uid, 'linxo.config', param_name)
        return { param_name: value }

    def _set_default(self, cr, uid, ids, context, param_name):
        """Set default username to use with linxo API"""
        if uid != SUPERUSER_ID and not self.pool['res.users'].has_group(cr, uid, 'base.group_erp_manager'):
            raise openerp.exceptions.AccessError(_("Only administrators can change the settings"))
        config = self.browse(cr, uid, ids[0], context)
        ir_values = self.pool.get('ir.values')
        value = getattr(config, param_name)
        ir_values.set_default(cr, SUPERUSER_ID, 'linxo.config', param_name, value)
linxo_config_settings()


class linxo_account(osv.osv):
    """ Bank Account stored on Linxo """
    _name = "linxo.account"
    _description = "Linxo Bank Account"
    _columns = {
        'name': fields.char('Account Name', size=120, required=True),
        'linxo_id' : fields.integer('Linxo Account ID', required=True),
        'journal_id': fields.many2one('account.journal', 'Bank Journal', ondelete='cascade'),
        'account_group_name': fields.char('Account Group Name', size=30, required=True),
        'account_number': fields.char('Account Number', size=30, required=True),
        'type': fields.char('Account Type', size=30, required=True),
    }
    _sql_constraints = [
        ('name', 'unique(name)', 'The name of the bank account must be unique'),
        ('account_number', 'unique(account_number)', 'The account number must be unique'),
        ('linxo_id', 'unique(linxo_id)', 'The account number must be unique')
    ]
    _order = 'name asc'
linxo_account()


class linxo_transaction(osv.osv):
    """ Bank Transaction stored on Linxo """
    _name = 'linxo.transaction'
    _columns = {
        'linxo_id': fields.integer('Linxo Transaction ID', required=True),
        'account_id': fields.many2one(
            'linxo.account', 'Linxo Account', ondelete='cascade'),
        'account_move_line_id': fields.many2one(
            'account.move.line', 'Account Move Line'),
        'amount': fields.float('Amount', digits_compute=dp.get_precision('Account'), required=True),
        'budget_date': fields.date('Budget Date', required=True),
        'date': fields.date('Date', required=True),
        'category' : fields.integer('Category'),
        'label': fields.char('Label', size=255, required=True),
        'notes': fields.char('Notes', size=255, required=True),
        'city' : fields.char('City', size=255),
        'original_label': fields.char('Original Label', size=255),
        'original_third_party': fields.char('Original Third Party', size=255),
        'journal_id': fields.related(
            'account_id', 'journal_id', type="many2one",
            relation="account.journal", string="Bank Journal",
            store=False),
    }

    _sql_constraints = [
        ('linxo_id', 'unique(linxo_id)', 'The account number must be unique'),
    ]

    _rec_name = 'label'
    _order = 'date desc'

    #def fields_view_get(self, cr, uid, view_id=None, view_type='form', context=None, toolbar=False, submenu=False):
    #    #if (not view_id) and (view_type=='form') and context and context.get('force_email', False):
    #    #    view_id = self.pool.get('ir.model.data').get_object_reference(cr, user, 'base', 'view_partner_simple_form')[1]
    #    #res = super(res_partner,self).fields_view_get(cr, user, view_id, view_type, context, toolbar=toolbar, submenu=submenu)

    #    #if view_type == 'form':
    #    #    res['arch'] = self.fields_view_get_address(cr, user, res['arch'], context=context)

    #    res = super(linxo_transaction, self).fields_view_get(cr, uid, view_id, view_type, context, toolbar, submenu=submenu)

    #    # Only on form view
    #    if view_type == 'form':
    #        for key in context:
    #            _logger.debug('context key %s value %s' % (key,context[key]))

    #        transaction = self.browse(cr, SUPERUSER_ID, uid, context=context)

    #        eview = etree.XML(res['arch'])
    #        for node in eview.xpath("//field[@name='account_move_line_id']"):
    #            extra_context = "{'tree_view_ref': 'linxo.view_linxo_moves_tree', 'search_view_ref': 'linxo.view_linxo_moves_search'}"
    #            #if transaction.amount > 0:
    #                #domain =  "[('credit', '=',%f )]" % abs(transaction.amount)
    #                #extra_context = extra_context + " 'search_default_credit':%f }" % abs(transaction.amount)
    #            #else:
    #                #domain =  "[('debit', '=',%f )]" % abs(transaction.amount)
    #                #extra_context = extra_context + " 'search_default_debit':%f }" % abs(transaction.amount)
    #            #node.set('domain', domain)
    #            node.set('context', extra_context)
    #        res['arch'] = etree.tostring(eview)

    #        _logger.debug('final res')
    #        _logger.debug(res['arch'])
    #    return res

    # on change account_move_line_id
    def apply_reconciliation(self, cr, uid, ids, context=None):
        """Mark account move as ok only if amount match
        Also mark invoice as paid, only if amount match
        """
        if not context:
            context = {}

        transactions = self.browse(cr, uid, ids, context=context)

        obj_move = self.pool.get('account.move')
        obj_invoice = self.pool.get('account.invoice')

        do_raise = True
        if 'batch' in context:
            do_raise = False

        for transaction in transactions:
            if transaction.account_move_line_id:
                account_move_line = transaction.account_move_line_id

                account_move = account_move_line.move_id

                # Check that balance is 0 and amount match
                if account_move.balance != 0.0:
                    if do_raise:
                        raise osv.except_osv(_("Error!"), _("Unable to apply reconciliation, the associated move is not balance"))
                    else:
                        continue
                if account_move.amount != abs(transaction.amount):
                    if do_raise:
                        raise osv.except_osv(_("Error!"), _("Unable to apply reconciliation, the associated move amount differs from the transaction"))
                    else:
                        continue

                # So far ok, if draft, make is as OK
                if account_move.state == 'draft':
                    _logger.debug('Marking account_move as validate')
                    obj_move.button_validate(cr, uid, [account_move.id], context=context)


    def search_reconciliation(self, cr, uid, ids, context=None):
        transactions = self.browse(cr, uid, ids, context=context)
        for transaction in transactions:

            # Already matched ?
            if transaction.account_move_line_id:
                continue

            # From journal we need to extract default debit and credit account
            journal = transaction.journal_id

            search_args = [
                ('journal_id', '=', journal.id),
            ]

            if transaction.amount > 0:
                search_args.append(('debit', '=', transaction.amount))
                search_args.append(('account_id', '=', journal.default_debit_account_id.id ))
            else:
                search_args.append(('credit', '=', -transaction.amount))
                search_args.append(('account_id', '=', journal.default_credit_account_id.id ))

            date_base = transaction.date
            date_test = [date_base]

            # TODO : limit to 5 ?
            for drift in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
                date = datetime.datetime.strptime(date_base,"%Y-%m-%d") + datetime.timedelta(days=int(drift))
                date_test.append(date.strftime("%Y-%m-%d"))

            obj_move_line = self.pool.get('account.move.line')
            move_line_ids = None

            # Perform search starting from today and go back in time
            # Transaction usually appear after date in openerp
            for date in date_test:
                final_search = list(search_args)
                final_search.append(('date', '=', date))
                _logger.debug('searching for date %s' % date)
                _logger.debug(final_search)
                move_line_ids = obj_move_line.search(cr, uid, final_search, context=context)
                if move_line_ids:
                    _logger.debug('Found account move line for date %s' % date)
                    # Extra check, do we already have a transaction with this id ?
                    if len(move_line_ids) == 1:
                        test_search = [('account_move_line_id', '=', move_line_ids[0])]
                        test_ids = self.search(cr, uid, test_search, context=context)
                        if not test_ids:
                            _logger.debug('This account.move.line is unsed, let\'s use it !')
                        else:
                            _logger.debug('This account.move.line is already use, skipping it')
                            continue
                    break

            if not move_line_ids:
                pass
            elif len(move_line_ids) > 1:
                _logger.debug('Find more than one account.move.line')
            else:

                vals = { 'account_move_line_id' : move_line_ids[0] }
                self.write(cr, uid, [transaction.id], vals, context=context)


    def write(self, cr, uid, ids, vals, context=None):
        """Override write to apply reconciliation immediatelly
        """
        if not context:
            context = {}

        res = super(linxo_transaction, self).write(cr, uid, ids, vals, context=context)
        self.apply_reconciliation(cr, uid, ids, context=context)
        return res


    def do_reconciliation(self, cr, uid, ids, context=None):
        """Perform reconciliation on all unmark transaction
        """
        self.search_reconciliation(cr, uid, ids, context=context)

    def open_wizard(self, cr, uid, ids, context=None):
        if context is None: 
            context = {}
        if not ids: 
            return False
        if not isinstance(ids, list): 
            ids = [ids]

        transaction = self.browse(cr, uid, ids, context=context)[0]

        vals = { 'transaction_id': ids[0], 'date': transaction.date }
        if transaction.amount > 0:
            vals['debit'] = transaction.amount
        else:
            vals['credit'] = -transaction.amount

        # Add transaction id to context
        context['transaction_id'] = ids[0]

        wizard_id = self.pool.get('linxo.reconcile').create(cr, uid, vals=vals, context=context)
        return {
            'name': 'Reconcile Wizard',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'linxo.reconcile',
            'res_id': wizard_id,
            'type': 'ir.actions.act_window',
            'target': 'new',
            'context': context,
        }

linxo_transaction()

class linxo_reconcile(osv.osv_memory):
    _name='linxo.reconcile'

    def _get_candidates(self, cr, uid, ids, field_name, arg, context):
        """Will return a list of ids of account.move.line according to amount & co
        """
        result = {}

        wizard = self.browse(cr, uid, ids, context=context)[0]
        wizard_id = ids[0]

        transaction = wizard.transaction_id
        if not transaction:
            return result

        _logger.debug('Got transaction %d' % transaction.id)

        search_args = [
            #('date', '=', wizard.date),
            ('journal_id', '=', transaction.journal_id.id),
        ]

        if wizard.credit:
            search_args.append(('credit', '=', wizard.credit))
        else:
            search_args.append(('debit', '=', wizard.debit))

        _logger.debug('Search criteria for account.move.line')
        _logger.debug(search_args)

        move_line_pool = self.pool.get('account.move.line')
        account_move_line_ids = move_line_pool.search(cr, uid, search_args, context=context)

        final_ids = []
        if account_move_line_ids:
            for line_id in account_move_line_ids:
                account_move_line = move_line_pool.browse(cr, uid, line_id, context=context)
                if account_move_line.move_id.state != 'posted':
                    final_ids.append(line_id)

        if final_ids:
            result[wizard_id] = final_ids
        elif account_move_line_ids:
            result[wizard_id] = account_move_line_ids
        else:
            #res[i] must be set to False and not to None because of XML:RPC
            # "cannot marshal None unless allow_none is enabled"
            result[wizard_id] = False

        return result

    def _get_invoices(self, cr, uid, ids, field_name, arg, context):
        """Will return a list of ids of unpaid account.invoice, matching amount 
        """
        result = {}

        wizard = self.browse(cr, uid, ids, context=context)[0]
        wizard_id = ids[0]

        transaction = wizard.transaction_id
        if not transaction:
            return result

        _logger.debug('Got transaction %d' % transaction.id)

        search_args = [
            ('state', '=', 'open')
        ]

        if wizard.credit:
            search_args.append(('amount_total', '=', wizard.credit))
        else:
            search_args.append(('amount_total', '=', wizard.debit))

        _logger.debug('Search criteria for account.invoice')
        _logger.debug(search_args)

        account_ids = self.pool.get('account.invoice').search(cr, uid, search_args, context=context)

        if account_ids:
            result[wizard_id] = account_ids
        else:
            #res[i] must be set to False and not to None because of XML:RPC
            # "cannot marshal None unless allow_none is enabled"
            result[wizard_id] = False

        return result


    _columns = {
        'date': fields.date('Date'),
        'debit': fields.float('Debit', digits_compute=dp.get_precision('Account')),
        'credit': fields.float('Credit', digits_compute=dp.get_precision('Account')),
        'transaction_id': fields.many2one('linxo.transaction', 'Original Transaction'),
        'candidates' : fields.function(_get_candidates, type='one2many', obj='account.move.line', method=True, string='Matching Transactions'),
        'invoices' : fields.function(_get_invoices, type='one2many', obj='account.invoice', method=True, string='Matching Unpaid Transaction'),
    }

    def find_candidates(self, cr, uid, ids, context=None):
        for key in context:
            _logger.debug('context key %s value %s' % (key,context[key]))

        return {
                'type': 'ir.actions.act_window_close',
         }
linxo_reconcile()

class account_move_line(osv.osv):
    _inherit = 'account.move.line'

    def _search_reconciled(self, cr, uid, obj, name, args, context=None):
        only_unreconciled = False
        for arg in args:
            if arg[0] == 'unreconciled' and arg[1] == '=' and arg[2] == True:
                only_unreconciled = True

        # Only linxo_account jorunal
        # Only id present in linxo transaction
        # Only account that match account_journal as defined on journal as defined in linxo_account ...
        query = 'SELECT id FROM account_move_line ' \
                'WHERE journal_id IN ' \
                '(SELECT journal_id FROM linxo_account) '

        if only_unreconciled:
            query = query + 'AND id NOT IN '
        else:
            query = query + 'AND id IN '

        query = query + '(SELECT account_move_line_id FROM linxo_transaction WHERE account_move_line_id > 0) ' \
                'AND account_id IN (SELECT default_debit_account_id FROM account_journal WHERE id IN (SELECT journal_id FROM linxo_account)) ' \
                'AND period_id IN (SELECT id FROM account_period WHERE state = \'draft\')'

        cr.execute(query)
        res = cr.fetchall()

        return [('id', 'in', [x[0] for x in res])]
        
    def _get_unreconciled(self, cr, uid, ids, field_name, arg, context):
        res = {}
        for i in ids:
            move_obj = self.pool.get('linxo.transaction')
            search = [('account_move_line_id', '=', i)]
            test_ids = move_obj.search(cr, uid, search, context=context)
            if test_ids:
                res[i] = False
            else:
                res[i] = True
        return res

    _columns = {
        'unreconciled' : fields.function(
            _get_unreconciled,
            fnct_search=_search_reconciled,
            type='boolean',
            method=True,
            string='Reconciled'),
    }

    def do_reconciliation(self, cr, uid, ids, context=None):
        if not context:
            context = {}

        if not 'transaction_id' in context:
            _logger.warning('do_reconciliation problem, context is fucked up')
            _logger.warning(context)
            raise osv.except_osv(_("Warning"), _("I dont have a transaction associated, this is weird."))

        transaction_id = context['transaction_id']
        vals = { 'account_move_line_id' : ids[0] }
        self.pool.get('linxo.transaction').write(cr, uid, [transaction_id], vals, context=context)

        return True
account_move_line()

class account_invoice(osv.osv):
    _inherit = 'account.invoice'

    def do_reconciliation(self, cr, uid, ids, context=None):
        if not context:
            context = {}

        if not 'transaction_id' in context:
            _logger.warning('do_reconciliation problem, context is fucked up')
            _logger.warning(context)
            raise osv.except_osv(_("Warning"), _("I dont have a transaction associated, this is weird."))

        transaction_id = context['transaction_id']
        transaction = self.pool.get('linxo.transaction').browse(cr, uid, transaction_id, context=context)

        invoice = self.browse(cr, uid, ids[0], context=context)
        move = invoice.move_id
        
        # First part, create voucher
        account = transaction.journal_id.default_credit_account_id or transaction.journal_id.default_debit_account_id
        period_id = self.pool.get('account.voucher')._get_period(cr, uid)
        partner_id = self.pool.get('res.partner')._find_accounting_partner(invoice.partner_id).id,

        voucher_data = {
            'partner_id': partner_id,
            'amount': abs(transaction.amount),
            'journal_id': transaction.journal_id.id,
            'period_id': period_id,
            'account_id': account.id,
            'type': invoice.type in ('out_invoice','out_refund') and 'receipt' or 'payment',
            'reference' : invoice.name,
        }

        _logger.debug('voucher_data')
        _logger.debug(voucher_data)

        voucher_id = self.pool.get('account.voucher').create(cr, uid, voucher_data, context=context)
        _logger.debug('test')
        _logger.debug(voucher_id)

        # Equivalent to workflow proform
        self.pool.get('account.voucher').write(cr, uid, [voucher_id], {'state':'draft'}, context=context)

        # Need to create basic account.voucher.line according to the type of invoice need to check stuff ...
        double_check = 0
        for move_line in invoice.move_id.line_id:
            # According to invoice type 
            if invoice.type in ('out_invoice','out_refund'):
                if move_line.debit > 0.0:
                    line_data = {
                        'name': invoice.number,
                        'voucher_id' : voucher_id,
                        'move_line_id' : move_line.id,
                        'account_id' : invoice.account_id.id,
                        'partner_id' : partner_id,
                        'amount_unreconciled': abs(move_line.debit),
                        'amount_original': abs(move_line.debit),
                        'amount': abs(move_line.debit),
                        'type': 'cr',
                    }
                    _logger.debug('line_data')
                    _logger.debug(line_data)

                    line_id = self.pool.get('account.voucher.line').create(cr, uid, line_data, context=context)
                    double_check += 1
            else:
                # In case of invoice with negative amount ...
                if move_line.credit > 0.0 and move_line.credit != move_line.tax_amount:
                    line_data = {
                        'name': invoice.number,
                        'voucher_id' : voucher_id,
                        'move_line_id' : move_line.id,
                        'account_id' : invoice.account_id.id,
                        'partner_id' : partner_id,
                        'amount_unreconciled': abs(move_line.credit),
                        'amount_original': abs(move_line.credit),
                        'amount': abs(move_line.credit),
                        'type': 'dr',
                    }
                    _logger.debug('line_data')
                    _logger.debug(line_data)

                    line_id = self.pool.get('account.voucher.line').create(cr, uid, line_data, context=context)
                    double_check += 1

        # Cautious check to see if we did ok
        if double_check == 0:
            _logger.warning(invoice)
            _logger.warning(voucher_id)
            raise osv.except_osv(_("Warning"), _("I did not create any voucher line"))
        elif double_check > 1:
            _logger.warning(invoice)
            _logger.warning(voucher_id)
            raise osv.except_osv(_("Warning"), _("I created multiple voucher line ??"))


        # Where the magic happen
        self.pool.get('account.voucher').button_proforma_voucher(cr, uid, [voucher_id], context=context)
        _logger.info('Invoice was mark as paid')

        # Final step mark the correct account_move _line
        voucher = self.pool.get('account.voucher').browse(cr, uid, voucher_id, context=context)

        search_args = [
            ('move_id', '=', voucher.move_id.id),
            ('account_id', '=', account.id),
        ]
        move_line_ids = self.pool.get('account.move.line').search(cr, uid, search_args, context=context)
        if len(move_line_ids) != 1:
            _logger.warning('Weird, we should have one')
            _logger.warning(move_line_ids)
        else:
            vals = { 'account_move_line_id' : move_line_ids[0] }
            self.pool.get('linxo.transaction').write(cr, uid, [transaction_id], vals, context=context)

        return True
account_invoice()
