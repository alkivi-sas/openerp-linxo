# -*- coding: utf-8 -*-
import logging

from openerp import api, fields, models
from linxo import Client as ApiClient

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
        if local_key in local._columns and linxo_key in linxo:
            value = getattr(local, local_key)
            if has_changed(value, linxo[linxo_key], data_type):
                new_value = format_linxo_data(linxo[linxo_key])
                setattr(local, local_key, new_value)
                if changes is None:
                    key_differs_string = u'key {} differs {} {}'.format(local_key, value, linxo[linxo_key])
                    _logger.debug(key_differs_string)
                    return True
    return changes


class linxo_sync(models.TransientModel):
    """Model not in database to handle the linxo synchronisation."""
    _name = 'linxo.sync'
    _description = 'Linxo Manual Sync'

    name = fields.Char('Name', size=48, default='Manual Sync')

    account_treated = fields.Integer('Number of account treated', default=0)
    account_updated = fields.Integer('Number of account updated', default=0)
    account_created = fields.Integer('Number of new account', default=0)
    transaction_treated = fields.Integer('Number of transaction treated', default=0)
    transaction_updated = fields.Integer('Number of transaction updated', default=0)
    transaction_created = fields.Integer('Number of new transaction', default=0)

    def do_reconciliation(self):
        transaction_obj = self.env['linxo.transaction']
        search_args = [('account_move_line_id', '=', None), ('reconciled', '=', False)]
        transactions_ids = transaction_obj.search(search_args)
        if transactions_ids:
            transaction_obj.search_reconciliation(transactions_ids)
            return True
        else:
            return False

    @api.multi
    def do_sync(self):
        """Perform sync with openerp server"""
        # batch context key will be use not to raise exeption
        self = self.with_context(batch=True)
        context = self.env.context

        # Will use .linxo.conf in odoo HOME directory
        self.client = ApiClient()

        # Statistics
        data = {
            'account_treated': 0,
            'account_updated': 0,
            'account_created': 0,
            'transaction_treated': 0,
            'transaction_updated': 0,
            'transaction_created': 0,
        }

        # First pass, create or update bankAccount in our database
        params = {'status': 'ACTIVE'}
        url = '/accounts'
        bank_accounts = self.client.get(url, **params)
        _logger.debug('Linxo bank accounts :')
        _logger.debug(bank_accounts)

        types_to_keep = ['CREDIT_CARD', 'CHECKINGS']
        for account in bank_accounts:
            if account['type'] not in types_to_keep:
                continue

            result = self._handle_bank_account(account)

            if result == 1:
                data['account_created'] += 1
            elif result == 2:
                data['account_updated'] += 1
            data['account_treated'] += 1

        # Stop here : init sync
        if 'account_only' in context:
            self.write(data)
            return self

        # Second pass, fetch operation from bankAccount
        for account in bank_accounts:
            if account['type'] not in types_to_keep:
                continue

            transactions = self._get_transactions(account=account, step=100)
            _logger.info('We fetched %d transactions' % len(transactions))

            for transaction in transactions:
                self._handle_bank_transaction(transaction)

                if result == 1:
                    data['transaction_created'] += 1
                elif result == 2:
                    data['transaction_updated'] += 1
                data['transaction_treated'] += 1

        self.write(data)
        return self

    def _handle_bank_transaction(self, transaction):
        """Replicate linxo transaction in our local database

        Take the description of linxo and check if the account exist in database.
        Update if necessary the information

        Return
        0 if no changes
        1 if created
        2 if updated
        """
        _logger.debug('handling transaction linxo_id=%s' % transaction['id'])

        transaction_obj = self.env['linxo.transaction']
        transaction_ids = transaction_obj.search([('linxo_id', '=', transaction['id'])])

        # Need to fetch associated account
        account_obj = self.env['linxo.account']
        account_ids = account_obj.search([('linxo_id', '=', transaction['account_id'])])
        if not account_ids:
            raise Exception('WTF ? We should have an account here')
        local_account = account_ids[0]

        def _get_translation_dict(data=None):
            if data is None:
                return {
                    'label': 'label',
                    'notes': 'notes',
                }
            elif data is 'int':
                return {'linxo_id': 'id'}
            if data is 'float':
                return {'amount': 'amount'}
            elif data == 'date':
                return {'date': 'date'}
            else:
                raise Exception('wrong data {}'.format(data))

        def _get_data_types():
            """Return data_type use to translation linxo data to database
            """
            return [None, 'int', 'float', 'date']

        def _get_values(account):
            """Return an hash with value to update in erp database
            """
            transaction_values = {'account_id': account.id}
            for data_type in _get_data_types():
                for local_key, linxo_key in _get_translation_dict(data_type).iteritems():
                    if linxo_key in transaction:
                        value = format_linxo_data(transaction[linxo_key], data_type)
                        transaction_values[local_key] = value
            return transaction_values

        if transaction_ids:
            local_transaction = transaction_ids

            to_return = 0
            for data_type in _get_data_types():
                temp_changes = check_changes(local_transaction, transaction, _get_translation_dict(data_type), data_type)
                if temp_changes:
                    transaction_values = _get_values(local_account)
                    local_transaction.write(transaction_values)
                    _logger.debug('updated transaction %d' % transaction_ids[0])
                    to_return = 2
                    break
            _logger.debug('nothing to do for transaction %d' % transaction_ids[0])

            if not local_transaction.account_move_line_id:
                local_transaction.search_reconciliation()

            return to_return

        else:
            transaction_values = _get_values(local_account)
            tr_id = transaction_obj.create(transaction_values)
            _logger.debug('created new transaction %d' % tr_id)
            transaction_obj.browse(tr_id).search_reconciliation()
            return 1

    def _handle_bank_account(self, account):
        """Replicate linxo bankAccount to our local database

        Take the description of linxo and check if the account exist in database.
        Update if necessary the information
i
        Return
        0 if no changes
        1 if created
        2 if updated
        """
        _logger.debug('handling account linxo_id %s' % account['id'])

        account_obj = self.env['linxo.account']
        account_ids = account_obj.search([('linxo_id', '=', account['id'])])

        def _get_translation_dict(data=None):
            """Link between our object name and linxo object
            """
            if data is None:
                return {'account_number': 'accountNumber',
                        'name': 'name',
                        'type': 'type'}
            elif data is 'int':
                return {'linxo_id': 'id'}
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
            local_account = account_ids
            for data_type in _get_data_types():
                temp_changes = check_changes(local_account, account, _get_translation_dict(data_type), data_type)
                if temp_changes:
                    account_values = _get_values()
                    local_account.write(account_values)
                    _logger.debug('updated account %d' % account_ids[0])
                    return 2
            _logger.debug('nothing to do for account %d' % account_ids[0])
            return 0
        else:
            account_values = _get_values()
            account_id = account_obj.create(account_values)
            _logger.debug('created new account %d' % account_id)
            return 1

    def _get_transactions(self, account, step=100):
        """Fetch latest operation on linxo, specific to one account."""
        context = self.env.context

        url = '/transactions'
        page = 1
        transactions = []
        stop = False

        while not stop:
            _logger.debug('Goint to fetch {0} transactions '.format(step) +
                          'for account {0} (id {1}) '.format(account['name'], account['id']) +
                          'starting at page {0}'.format(page))
            params = {'account_id': account['id'], 'page': page, 'limit': step}
            data = self.client.get(url, **params)
            transactions += data
            page += 1

            # Conditions to stop max is reached
            if len(data) < step:
                _logger.debug('We fetched more than the max, stopping here')
                stop = True
            # Complete sync in progress
            elif 'complete_sync' in context:
                _logger.debug('Complete sync, gogogogo')
                continue
            # Look in database lowest id (older transaction)
            else:
                sorted_transactions = sorted(transactions, key=lambda transaction: transaction['id'])
                lowest_id = int(sorted_transactions[0]['id'])

                # Do we have a linxo.transaction with that id ?
                transaction_obj = self.env['linxo.transaction']
                transaction_ids = transaction_obj.search([('linxo_id', '=', lowest_id)])
                if transaction_ids:
                    stop = True
        return transactions
